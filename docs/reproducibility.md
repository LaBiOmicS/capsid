# Reproducibility

A Capsid image is a function of three inputs:

1. the **recipe** (`recipe.yaml` or stack `.yaml`);
2. its **lockfile** (`pixi.lock` / `requirements.lock`), or the `sha256` of its source archives;
3. the **Capsid version** that renders the Dockerfile, which fixes every base image, builder image and
   the apt snapshot.

With the same three inputs, Capsid renders a byte-identical Dockerfile, and a build with
`rewrite-timestamp` produces the same image digest. This page explains each mechanism and how to
check the result.

- [What is pinned](#what-is-pinned)
- [Lockfiles](#lockfiles)
- [Debian packages](#debian-packages)
- [Timestamps](#timestamps)
- [Publishing](#publishing)
- [Verifying a rebuild](#verifying-a-rebuild)
- [Updating pins](#updating-pins)
- [What is not covered](#what-is-not-covered)

---

## What is pinned

| Input | How it is pinned | Where |
| :--- | :--- | :--- |
| Dockerfile syntax | `docker/dockerfile:1.4@sha256:…` | first line of every Dockerfile |
| Runtime and builder images | `image:tag@sha256:…` (Debian, Python, pixi, uv, Rust) | `src/capsid/builders/dockerfile_gen.py` |
| Debian packages | `snapshot.debian.org` timestamp | `DEFAULT_APT_SNAPSHOT`, or `build.apt_snapshot` |
| Conda packages | `pixi.lock`: every artefact URL with SHA-256, per platform | next to the recipe |
| PyPI packages | `requirements.lock`: every distribution with `--hash=sha256:` | next to the recipe |
| Source archives | `build.sha256`, checked with `sha256sum -c` | recipe |
| Prebuilt binaries | `build.sha256_amd64` / `build.sha256_arm64` | recipe |
| Rust dependencies | the project's own `Cargo.lock` (`cargo install --locked`) | inside the source archive |
| File and image timestamps | `SOURCE_DATE_EPOCH` (`build.source_date_epoch`, default `1700000000`) | recipe |
| Embedded metadata | generated from the recipe only (no "now", no host data) | `/etc/capsid/` |

The generated Dockerfile embeds the lockfile and all metadata as heredocs. It is the complete build
input: archive it, and the build can be repeated without Capsid.

## Lockfiles

```bash
capsid lock recipes/samtools/1.25/recipe.yaml
capsid lock stacks/variant-calling-stack.yaml
```

`capsid lock` runs the solver inside the same digest-pinned pixi or uv image that the build uses, so
the lockfile matches what the build can install:

- **pixi** resolves the conda environment for every platform in `platforms` and writes `pixi.lock`.
  The build runs `pixi install --locked`, which fails if the manifest and the lockfile disagree.
- **uv** compiles a `requirements.lock` with hashes for the recipe's Python version. The build runs
  `uv pip install --require-hashes`, which refuses any distribution whose hash is not in the file.

Without a lockfile the build still works, but dependencies are resolved at build time and the image
records what it got (`/etc/capsid/requirements.lock` or `/etc/capsid/pixi.lock`). The linter warns
about it (`W014`) and reports lockfiles that no longer match the recipe (`E016`, `E017`).

## Debian packages

Debian's mirrors change continuously, so `apt-get install` alone is not reproducible. Every builder
and runtime stage that uses Capsid's default images rewrites the apt sources to a fixed
[snapshot.debian.org](https://snapshot.debian.org) timestamp:

```
URIs: http://snapshot.debian.org/archive/debian/20261115T000000Z
Suites: bookworm bookworm-updates
...
URIs: http://snapshot.debian.org/archive/debian-security/20261115T000000Z
Suites: bookworm-security
```

Package integrity is still guaranteed by Debian's signed `Release` files (`Signed-By:` the Debian
archive keyring); `Check-Valid-Until: no` is required because snapshot `Release` files are old by
design. apt retries are enabled because the snapshot service rate-limits.

A recipe can pin its own timestamp with `build.apt_snapshot: 20260301T000000Z`, or opt out with
`latest`. With a custom `base_image` the snapshot is not configured.

## Timestamps

`SOURCE_DATE_EPOCH` is passed to BuildKit as a build argument and declared in every stage. It sets the
image creation time and the timestamps of the embedded SBOM and RO-Crate. Build tools that honour
it (GCC, Python's bytecode compiler, many packagers) use it as well.

## Publishing

`capsid build --push` (and the Capsid Hub publish workflow) runs, after the test passed:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f .capsid-build/<name>/Dockerfile --build-arg SOURCE_DATE_EPOCH=<epoch> \
  --provenance=false \
  --output type=image,name=<tag>,push=true,unpack=false,rewrite-timestamp=true \
  .capsid-build/<name>
```

- `rewrite-timestamp=true` clamps the modification time of every file in every layer to
  `SOURCE_DATE_EPOCH`, so layers built at different times are byte-identical.
- `--provenance=false` keeps BuildKit's own provenance attestation (which contains build timestamps)
  out of the image index; the hub attaches SLSA provenance separately, after the push.
- `unpack=false` is required by the Docker driver together with `rewrite-timestamp`.

With Podman, the equivalent is `podman build --manifest <tag> --source-date-epoch <epoch>
--rewrite-timestamp` followed by `podman manifest push`.

Foreign architectures are built with QEMU emulation unless native builders are configured. Capsid Hub
publishes from an amd64 runner and then pulls the pushed digest and runs the recipe's test on native
amd64 **and** arm64 runners.

## Verifying a rebuild

Rebuild a recipe twice into OCI archives and compare the manifests:

```bash
capsid dockerfile recipes/seqkit/2.8.2/recipe.yaml -o /tmp/seqkit.Dockerfile
for i in 1 2; do
  docker buildx build --no-cache --platform linux/amd64 -f /tmp/seqkit.Dockerfile \
    --build-arg SOURCE_DATE_EPOCH=1700000000 --provenance=false \
    --output type=oci,dest=/tmp/seqkit-$i.tar,rewrite-timestamp=true /tmp
done
for i in 1 2; do tar -xOf /tmp/seqkit-$i.tar index.json | sha256sum; done
```

The two digests are identical. To check a published image, rebuild it with the Capsid version the hub
used (the `CAPSID_REF` commit in the hub's workflows; also recorded in the `io.capsid.version` label)
and compare with `docker buildx imagetools inspect <image>`.

## Updating pins

Pins age: base images receive security fixes and new apt snapshots contain updated packages. In the
Capsid repository, `scripts/bump_pins.py` re-resolves every `image:tag@sha256:` literal **for the same
tag** and sets `DEFAULT_APT_SNAPSHOT` to today:

```bash
python scripts/bump_pins.py           # rewrite the pins and print the changes
python scripts/bump_pins.py --check   # exit 1 if any pin is outdated
```

The `bump-pins` workflow runs it every Monday, runs the unit tests and two locked smoke builds (one
pixi, one uv, each with its container test), and opens a pull request. Changing an image *tag*
(for example a new pixi or Python minor version) stays a deliberate, reviewed change.

A new Capsid version therefore produces different digests for the same recipe. Capsid Hub records
which engine commit it used (`CAPSID_REF`), and a recipe can freeze its Debian packages
independently with `build.apt_snapshot`.

## What is not covered

- **Build tools that ignore `SOURCE_DATE_EPOCH`** or embed build paths, host names or random values
  can still produce differing bytes in source builds. Conda and PyPI packages are installed from
  pre-built, hash-checked artefacts and are not affected.
- **Unlocked recipes** (no lockfile) resolve their dependencies at build time.
- **Custom base images** are only as reproducible as their pinning (`W017` without a digest) and
  get no apt snapshot.
- **Upstream disappearance**: a pinned artefact that is deleted upstream (a yanked PyPI release, a
  moved source URL) makes the recipe unbuildable, although published images remain available. The
  embedded Dockerfile inputs and SBOM document exactly what was used.
