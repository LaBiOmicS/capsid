# Capsid

**Reproducible, hardened containers for scientific software.**

[![CI](https://github.com/LaBiOmicS/capsid/actions/workflows/ci.yaml/badge.svg)](https://github.com/LaBiOmicS/capsid/actions/workflows/ci.yaml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Capsid turns a short, declarative `recipe.yaml` into a small, non-root, multi-architecture OCI image,
together with ready-to-use Nextflow, Snakemake and Apptainer integrations. Builds are pinned end to
end, so the same recipe and lockfile produce the same image digest.

Developed at [LaBiOmicS](https://github.com/LaBiOmicS), Universidade de Mogi das Cruzes (UMC), Brazil.
Community recipes and published images live in [Capsid Hub](https://github.com/LaBiOmicS/capsid-hub);
the hub website is <https://labiomics.github.io/capsid-hub/>.

---

## Contents

- [Why Capsid](#why-capsid)
- [Installation](#installation)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Documentation](#documentation)
- [Project status](#project-status)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

---

## Why Capsid

| | What Capsid does |
| :--- | :--- |
| **Small images** | Multi-stage builds: compilers, solvers and caches stay in builder stages; the runtime stage holds only the resolved environment. |
| **Seven build types** | Conda packages via [pixi](https://pixi.sh) (Bioconda + conda-forge), PyPI packages via [uv](https://docs.astral.sh/uv/), source builds with `autotools`, `make`, `cmake` or `rust-cargo` from a checksum-verified archive, and prebuilt `binary` releases. |
| **Reproducible** | Base and builder images pinned by digest, apt frozen to a `snapshot.debian.org` timestamp, conda/PyPI closures locked with hashes, `SOURCE_DATE_EPOCH` everywhere. Rebuilding a locked recipe yields the same image digest. |
| **Hardened by default** | Numeric non-root user `1000:1000`, `tini` as PID 1, root-owned read-only tool prefixes, no package manager caches. |
| **HPC ready** | Standard mount points (`/scratch`, `/data`, `/work`, `/lustre`, `/shared`), Apptainer/Singularity `.sif` export, and runtime settings that work with `--cleanenv`. |
| **Workflow ready** | Generates Nextflow DSL2 modules, `nextflow.config` profiles, Snakemake rules and Apptainer definition files. |
| **Self-describing** | Every image embeds its recipe, an SPDX 2.3 SBOM, RO-Crate 1.1 metadata, workflow modules and the lockfile under `/etc/capsid/`, plus OCI labels. |
| **Multi-tool stacks** | Combine several recipes into one image with a single shared environment (`capsid stack`). |
| **Supply chain** | Recipe linter, published JSON Schema, and a hub pipeline that signs images with Sigstore Cosign and attaches SBOM and SLSA provenance attestations. |

---

## Installation

Requirements:

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- Docker with BuildKit (buildx 0.13+), or Podman, to build images
- Optional: Apptainer or Singularity, to produce `.sif` images

```bash
uv tool install git+https://github.com/LaBiOmicS/capsid.git
capsid --help
```

For development, see [docs/development.md](docs/development.md).

---

## Quick start

```bash
# 1. Generate a recipe from an upstream package (Bioconda, PyPI or GitHub)
capsid init bioconda:samtools@1.25          # -> recipes/samtools/1.25/recipe.yaml

# 2. Pin the full dependency closure (commit the lockfile with the recipe)
capsid lock recipes/samtools/1.25/recipe.yaml

# 3. Check the recipe
capsid lint recipes/samtools/1.25/recipe.yaml

# 4. Build the image and run the recipe's test
capsid build recipes/samtools/1.25/recipe.yaml

# 5. Use it in a pipeline
capsid export nextflow recipes/samtools/1.25/recipe.yaml -o modules/samtools.nf
```

A recipe is a small YAML document:

```yaml
name: samtools
version: '1.25'
description: Tools for dealing with SAM, BAM and CRAM files
homepage: https://github.com/samtools/samtools
license: MIT
platforms: [linux/amd64, linux/arm64]
build:
  type: pixi-conda
  conda_packages: [samtools==1.25]
  channels: [conda-forge, bioconda]
test:
  command: samtools --help
```

Images published from Capsid Hub can be used directly:

```bash
docker run --rm ghcr.io/labiomics/capsid/samtools:1.25 samtools --version
apptainer exec docker://ghcr.io/labiomics/capsid/samtools:1.25 samtools --version
```

Full walkthrough: [docs/getting-started.md](docs/getting-started.md).

---

## How it works

```
recipe.yaml ─┐
pixi.lock /  ├─► capsid ─► Dockerfile ─► BuildKit / Podman ─► OCI image (amd64, arm64)
requirements │   (templates)                                  ├─ /etc/capsid/: recipe, SBOM, RO-Crate,
.lock       ─┘                                                │  module.nf, rule.smk, lockfile
                                                              ├─ test (capsid test)
                                                              └─ push, sign, attest (Capsid Hub CI)
```

1. The recipe is validated against the data model (published as JSON Schema).
2. A multi-stage Dockerfile is rendered from templates: a builder stage for the recipe's build type
   and a single shared hardened runtime stage.
3. The image is built with BuildKit or Podman, then the recipe's test runs inside it.
4. With `--push`, the image is rebuilt for every platform of the recipe and pushed with layer
   timestamps rewritten to `SOURCE_DATE_EPOCH`.

Details: [docs/architecture.md](docs/architecture.md) and [docs/image-design.md](docs/image-design.md).

---

## Documentation

| Guide | Contents |
| :--- | :--- |
| [Getting started](docs/getting-started.md) | Install, write or generate a recipe, build, test, export |
| [Recipe reference](docs/recipe-reference.md) | Every field of `recipe.yaml` and `stack.yaml` |
| [CLI reference](docs/cli-reference.md) | Every command and option |
| [Image design](docs/image-design.md) | Runtime layout, user, environment, labels, embedded metadata |
| [Reproducibility](docs/reproducibility.md) | Pinning, lockfiles, apt snapshots, bit-for-bit rebuilds |
| [Security](docs/security.md) | Hardening, linter rules, signing and attestations |
| [Workflow integration](docs/workflow-integration.md) | Nextflow, Snakemake, Apptainer and HPC usage |
| [Architecture](docs/architecture.md) | Code layout, build pipeline, templates, golden tests |
| [Development](docs/development.md) | Local setup, tests, golden files, maintenance tasks |
| [Roadmap](docs/roadmap.md) | Planned work |

---

## Project status

Capsid is in active development (version 0.1). The recipe format and CLI may still change; such
changes are recorded in the [changelog](CHANGELOG.md). Known gaps are listed in the
[roadmap](docs/roadmap.md).

---

## Contributing

Contributions to the engine are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). To package a tool,
contribute a recipe to [Capsid Hub](https://github.com/LaBiOmicS/capsid-hub/blob/main/CONTRIBUTING.md).

## Security

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © LaBiOmicS
