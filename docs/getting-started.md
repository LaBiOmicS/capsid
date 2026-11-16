# Getting started

This guide takes a tool from an upstream package to a tested image and a workflow module.

- [1. Install](#1-install)
- [2. Create a recipe](#2-create-a-recipe)
- [3. Lock the dependencies](#3-lock-the-dependencies)
- [4. Lint](#4-lint)
- [5. Inspect the Dockerfile](#5-inspect-the-dockerfile)
- [6. Build and test](#6-build-and-test)
- [7. Use the image in a workflow](#7-use-the-image-in-a-workflow)
- [8. Combine tools in a stack](#8-combine-tools-in-a-stack)
- [9. Publish to Capsid Hub](#9-publish-to-capsid-hub)

---

## 1. Install

```bash
uv tool install git+https://github.com/LaBiOmicS/capsid.git
capsid --help
```

You also need Docker (with BuildKit) or Podman. Apptainer or Singularity is optional and only
needed to produce `.sif` files locally.

---

## 2. Create a recipe

### Generate it from upstream

`capsid init` inspects an upstream package and writes `recipes/<tool>/<version>/recipe.yaml`:

```bash
capsid init bioconda:samtools@1.25     # conda package from Bioconda -> pixi-conda recipe
capsid init pypi:cutadapt@5.2          # PyPI package               -> uv-pypi recipe
capsid init github:lh3/minimap2        # GitHub release             -> source recipe (build system detected)
capsid init cutadapt                   # no prefix: tries PyPI, then Bioconda, then GitHub
```

Useful options: `--version/-v` to choose a version, `--out/-o` to choose the output path and
`--dry-run` to print the YAML instead of writing it. Always review a generated recipe: description,
license and test command come from upstream metadata and may need editing. For GitHub, Capsid
downloads the release (or tag) archive, records its SHA-256, and picks `rust-cargo`, `cmake`,
`autotools` or `make` from the files in the repository; build dependencies may still need to be
added by hand.

### Or write it by hand

Conda package (most bioinformatics tools):

```yaml
name: bwa
version: 0.7.19
description: Burrows-Wheeler aligner for short-read alignment against a large reference genome
homepage: https://github.com/lh3/bwa
license: GPL-3.0-only
platforms: [linux/amd64, linux/arm64]
build:
  type: pixi-conda
  conda_packages: [bwa==0.7.19]
  channels: [conda-forge, bioconda]
test:
  command: bwa 2>&1 | grep -q "Version"
```

PyPI package:

```yaml
name: cutadapt
version: '5.2'
description: Adapter trimming and other preprocessing of high-throughput sequencing reads
homepage: https://pypi.org/project/cutadapt/
license: MIT
build:
  type: uv-pypi
  package: cutadapt==5.2
  python_version: '3.12'
test:
  command: cutadapt --version
```

Compilation from source (the archive checksum is mandatory):

```yaml
name: mytool
version: 2.1.0
description: Example tool compiled with ./configure && make
license: MIT
build:
  type: autotools
  source: https://example.org/mytool-2.1.0.tar.gz
  sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  configure_args: [--disable-static]
  dependencies:
    build: [zlib1g-dev]
    run: [zlib1g]
test:
  command: mytool --version
```

Every field is described in the [recipe reference](recipe-reference.md). Editors that support
JSON Schema can validate recipes as you type; see [Recipe schema](recipe-reference.md#json-schema).

---

## 3. Lock the dependencies

```bash
capsid lock recipes/samtools/1.25/recipe.yaml
```

This resolves the complete dependency closure inside the same digest-pinned pixi or uv image used
by the build and writes it next to the recipe:

- `pixi.lock` for `pixi-conda` recipes (every platform listed in `platforms`);
- `requirements.lock` (with hashes) for `uv-pypi` recipes.

Commit the lockfile together with the recipe. Without it the build still works, but transitive
dependencies are resolved at build time and can change between builds (the linter reports
`W014_MISSING_LOCKFILE`).

---

## 4. Lint

```bash
capsid lint recipes/samtools/1.25/recipe.yaml   # one recipe
capsid lint stacks/variant-calling-stack.yaml   # one stack
capsid lint .                                    # every recipe and stack in a tree
capsid lint . --catalog catalog.json --strict    # also flag published versions; fail on warnings
```

Errors make the command fail; warnings are reported but only fail it with `--strict`.

The linter validates the schema, the directory layout (`recipes/<name>/<version>/`), pinning,
lockfiles, source checksums, test quality and known dangerous build commands. The rules are listed
in [Security](security.md#recipe-linter).

---

## 5. Inspect the Dockerfile

```bash
capsid dockerfile recipes/samtools/1.25/recipe.yaml
capsid dockerfile recipes/samtools/1.25/recipe.yaml -o Dockerfile
```

The Dockerfile is self-contained: lockfiles and metadata are embedded as heredocs, so it can be
built anywhere with `docker buildx build`.

---

## 6. Build and test

```bash
capsid build recipes/samtools/1.25/recipe.yaml
```

`capsid build` renders the Dockerfile into `.capsid-build/<tool>/`, builds the image for the host
architecture (tag `<tool>:<version>` unless `--tag` is given), and runs the recipe's test. A failing
test exits with code 2.

Common variations:

```bash
capsid build recipe.yaml --tag ghcr.io/me/samtools:1.25   # custom tag
capsid build recipe.yaml --apptainer                      # also write samtools-1.25.sif
capsid build recipe.yaml --engine podman                  # force Podman
capsid build recipe.yaml --no-cache                       # ignore the build cache
capsid build recipe.yaml --push --tag ghcr.io/me/samtools:1.25
```

`--push` publishes only after the test passed. It rebuilds the image for every platform listed in
the recipe and pushes a multi-architecture manifest; building for a foreign architecture requires
QEMU/binfmt or native builders. See [Reproducibility](reproducibility.md#publishing).

Run the test again later, or against another engine:

```bash
capsid test recipes/samtools/1.25/recipe.yaml
capsid test recipes/samtools/1.25/recipe.yaml --image ghcr.io/labiomics/capsid/samtools:1.25
capsid test recipes/samtools/1.25/recipe.yaml --engine apptainer --sif samtools-1.25.sif
```

---

## 7. Use the image in a workflow

```bash
capsid export nextflow  recipes/samtools/1.25/recipe.yaml -o modules/samtools.nf
capsid export snakemake recipes/samtools/1.25/recipe.yaml -o rules/samtools.smk
capsid export config    --target recipes/samtools/1.25/recipe.yaml -o nextflow.config
capsid export def       recipes/samtools/1.25/recipe.yaml -o samtools.def
capsid export sbom      recipes/samtools/1.25/recipe.yaml -o samtools.spdx.json
```

By default the exports reference `ghcr.io/labiomics/capsid/<tool>:<version>`; use `--registry` to
point to your own registry. The generated modules are starting points; adapt inputs, outputs and the
command line to your pipeline. More in [Workflow integration](workflow-integration.md).

---

## 8. Combine tools in a stack

A stack puts several recipes into one image with a single conda environment and/or a single Python
virtual environment:

```yaml
# stacks/variant-calling-stack.yaml
name: variant-calling-stack
version: 1.0.0
description: Samtools and BCFtools for variant calling and VCF manipulation
components:
  - recipe: samtools
    version: "1.25"
  - recipe: bcftools
    version: "1.25"
test:
  command: samtools --version && bcftools --version
```

```bash
capsid lock stacks/variant-calling-stack.yaml     # -> stacks/variant-calling-stack.pixi.lock
capsid stack build stacks/variant-calling-stack.yaml
```

Components are looked up by name and version in `$CAPSID_RECIPES_PATH`, `./recipes`,
`./capsid-hub/recipes` and `../capsid-hub/recipes`. Because all conda packages share one
environment, their versions must be mutually compatible (for example samtools and bcftools of the
same htslib release). Stacks support `pixi-conda` and `uv-pypi` components.

---

## 9. Publish to Capsid Hub

```bash
capsid submit recipes/samtools/1.25/recipe.yaml          # fork + branch + pull request via gh
capsid submit recipes/samtools/1.25/recipe.yaml --web    # pre-filled issue in the browser
```

The hub CI lints, builds and tests the recipe on the pull request; after merge it publishes a signed
multi-architecture image. See the
[Capsid Hub contribution guide](https://github.com/LaBiOmicS/capsid-hub/blob/main/CONTRIBUTING.md).
