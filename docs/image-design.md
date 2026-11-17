# Image design

This page describes what a Capsid image contains and why. Everything here is produced by the
templates in `src/capsid/templates/dockerfile/`; use `capsid dockerfile <recipe>` to see the exact
Dockerfile for a recipe.

- [Build stages](#build-stages)
- [Runtime base](#runtime-base)
- [Filesystem layout](#filesystem-layout)
- [User and permissions](#user-and-permissions)
- [Environment](#environment)
- [Entrypoint](#entrypoint)
- [Embedded metadata](#embedded-metadata)
- [Labels](#labels)
- [Size](#size)
- [Limitations](#limitations)

---

## Build stages

Every Dockerfile has one or more **builder** stages and one **runtime** stage. Compilers, solvers,
download tools and caches exist only in the builders; the runtime receives the installed tool tree
with `COPY --from`.

```
pixi-conda      ghcr.io/prefix-dev/pixi ─────────┐  pixi install --locked
uv-pypi         ghcr.io/astral-sh/uv (binary) ───┤  uv pip install --require-hashes
                python:3.X-slim-bookworm ────────┤
autotools/make/ debian:bookworm-slim ────────────┤  download, sha256sum -c, build, strip
cmake/binary                                     │
rust-cargo      rust:1.X-slim-bookworm ──────────┤  cargo install --locked
                                                 ▼
                            runtime: debian:bookworm-slim (or python:3.X-slim-bookworm)
```

The first line of every Dockerfile pins the BuildKit frontend by digest
(`# syntax=docker/dockerfile:1.4@sha256:…`), so the Dockerfile syntax itself cannot change under a
recipe.

A stack has one pixi builder and/or one uv builder that install all components together.

## Runtime base

| Build type | Runtime image |
| :--- | :--- |
| `uv-pypi` | `python:<python_version>-slim-bookworm` (the same image as its builder, so the virtual environment's interpreter exists in both) |
| all others | `debian:bookworm-slim` |
| any, with `build.base_image` | the given image |

Both are pinned by digest. apt in the builder and runtime stages is pointed at a fixed
`snapshot.debian.org` timestamp; see [Reproducibility](reproducibility.md#debian-packages).

The runtime installs only `tini`, `ca-certificates`, `bash` and the recipe's `dependencies.run`, with
`--no-install-recommends`, and then deletes the apt lists, logs and caches.

## Filesystem layout

| Path | Contents |
| :--- | :--- |
| `/opt/pixi/.pixi/envs/default` | Conda environment (`pixi-conda` and stacks). Conda packages embed their installation prefix, so the environment is installed at this path in the builder and copied to the same path. |
| `/opt/conda-env` | Symbolic link to the conda environment. |
| `/opt/venv` | Python virtual environment (`uv-pypi` and stacks). |
| `/opt/<name>` | Install prefix of source and binary builds (`build.install_prefix`). |
| `/etc/capsid/` | Embedded metadata; see [Embedded metadata](#embedded-metadata). |
| `/home/bio` | Home and default working directory of user `bio`. |
| `/scratch`, `/data`, `/work`, `/lustre`, `/shared` | Empty mount points common on HPC systems, mode `1777`, so bind mounts work without creating directories on read-only images. |
| `/tmp` | Mode `1777`; also used for caches (see [Environment](#environment)). |

The `bin/` directory of each prefix is on `PATH`.

## User and permissions

- Images run as user **`bio`**, UID/GID **1000:1000**. The `USER` instruction uses the numeric form
  so Kubernetes `runAsNonRoot` can verify it.
- Tool prefixes are owned by root and made world-readable and executable (`chmod -R a+rX`), but are
  not writable by `bio`: a running tool cannot modify itself.
- Nothing is installed setuid, and no `sudo` is present (`sudo` in `build_commands` is a lint error).
- Apptainer and Singularity run the image as the calling user; the layout works for any UID because
  the tool trees are world-readable and the writable locations are `1777`.

## Environment

```dockerfile
ENV PATH="<prefix>/bin:$PATH" \
    CONDA_PREFIX="/opt/pixi/.pixi/envs/default"   # conda images only
    HOME="/home/bio" \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    XDG_CACHE_HOME="/tmp/.cache" \
    PYTHONPYCACHEPREFIX="/tmp/pycache" \
    NUMBA_CACHE_DIR="/tmp/numba" \
    MPLCONFIGDIR="/tmp/matplotlib"
```

The cache variables send caches to `/tmp`, which is writable for every UID, so tools that write
caches (Numba, Matplotlib, Python bytecode) work under Apptainer, read-only root filesystems and
arbitrary `--user` values. Variables from `build.env` are added to the same instruction.

`PATH` is also written to `/etc/profile.d/capsid.sh` (login shells) and `/etc/environment` (PAM
sessions), because some HPC launchers start a login shell or clear the environment
(`apptainer exec --cleanenv`).

## Entrypoint

```dockerfile
USER 1000:1000
WORKDIR /home/bio
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash"]
```

[`tini`](https://github.com/krallin/tini) runs as PID 1: it forwards signals (so `docker stop` and
workflow-engine cancellation terminate the tool promptly) and reaps zombie processes. Commands are
passed as arguments, as usual: `docker run IMAGE samtools --version`.

Apptainer, Singularity and Nextflow/Snakemake with these runtimes ignore the entrypoint and execute
the command directly, which is also fine.

## Embedded metadata

Every image carries a self-description under `/etc/capsid/`:

| File | Contents |
| :--- | :--- |
| `recipe.yaml` | The complete recipe the image was built from, with all defaults filled in. Stacks have `stack.yaml`. |
| `pixi.lock` / `requirements.lock` | The resolved environment: the lockfile used by the build, or (`uv-pypi` without a lockfile) the output of `uv pip freeze`. |
| `sbom.spdx.json` | SPDX 2.3 SBOM: the tool and every locked conda artefact or PyPI distribution with its URL, SHA-256 and package URL (purl); for source and binary builds, the verified upstream archives. |
| `ro-crate-metadata.json` | [RO-Crate 1.1](https://www.researchobject.org/ro-crate/) description of the image (`SoftwareApplication`), with license, DOI, EDAM terms, and the files above. Extended with the recipe's `ro_crate` field. |
| `module.nf` | Nextflow DSL2 process for the tool. |
| `rule.smk` | Snakemake rule for the tool. |

Read them from any copy of the image, even without the original repository:

```bash
docker run --rm ghcr.io/labiomics/capsid/samtools:1.25 cat /etc/capsid/recipe.yaml
apptainer exec samtools-1.25.sif cat /etc/capsid/sbom.spdx.json
```

All files are generated from the recipe, with timestamps derived from `SOURCE_DATE_EPOCH`, so they do
not break reproducibility.

## Labels

| Label | Value |
| :--- | :--- |
| `org.opencontainers.image.title` | `name` |
| `org.opencontainers.image.version` | `version` |
| `org.opencontainers.image.description` | `description` |
| `org.opencontainers.image.url` | `homepage` |
| `org.opencontainers.image.licenses` | `license` |
| `org.opencontainers.image.vendor` | `LaBiOmicS, Universidade de Mogi das Cruzes (UMC)` |
| `org.opencontainers.image.source` | `https://github.com/LaBiOmicS/capsid-hub` (GitHub Container Registry links the package to this repository) |
| `org.opencontainers.image.doi` | `doi`, if set |
| `io.capsid.version` | Capsid version that generated the image |
| `io.capsid.engine` | Build type |
| `io.capsid.fair.*` | Paths of the embedded metadata files; EDAM topics and operations |
| `io.capsid.hpc`, `io.capsid.orchestrators` | Supported runtimes and workflow managers |
| `io.capsid.type`, `io.capsid.components` | Stacks only: `stack` and the list of `name@version` components |

Plus every entry of the recipe's `labels`.

```bash
docker inspect --format '{{json .Config.Labels}}' ghcr.io/labiomics/capsid/samtools:1.25
```

## Size

Uncompressed sizes reported by `docker image inspect` (linux/amd64):

| Image | Build type | Size |
| :--- | :--- | ---: |
| seqkit 2.8.2 | `pixi-conda` | 147 MB |
| cutadapt 5.2 | `uv-pypi` | 185 MB |
| samtools 1.25 | `pixi-conda` | 291 MB |
| bcftools 1.25 | `pixi-conda` | 457 MB (Perl, GSL and plotting dependencies) |

Most of the size is the tool's own dependency tree; the rest is the runtime base
(`debian:bookworm-slim` or `python:<version>-slim-bookworm`) with `tini`, `bash` and
`ca-certificates`. Registries store layers compressed, so downloads are considerably smaller.

To reduce an image:

- prefer a conda package without optional heavy dependencies, or list only what you need;
- for source builds, keep `dependencies.run` to the shared libraries the binary actually links
  (`ldd` inside the builder tells you which);
- use a stack instead of several images when tools are always used together.

## Limitations

- **Conda activation scripts are not run.** A few conda packages set environment variables in
  `etc/conda/activate.d/` (for example `JAVA_HOME` or `GDAL_DATA`). Capsid sets `PATH` and
  `CONDA_PREFIX` but does not execute these scripts. If a tool needs such a variable, set it in
  `build.env`.
- **Debian bookworm only.** Package names in `dependencies` are Debian bookworm names; a custom
  `base_image` must be Debian-based.
- **Linux images only**, for `linux/amd64` and `linux/arm64`.
