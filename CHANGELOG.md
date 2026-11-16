# Changelog

All notable changes to Capsid are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Before 1.0, minor versions may change the recipe format.

## [Unreleased]

### Added

- Source build types `make`, `cmake` and `rust-cargo`, and the `binary` type for prebuilt releases,
  next to `autotools`, `pixi-conda` and `uv-pypi`.
- `capsid init github:owner/repo` detects the build system and honours `--version`.
- Linter with stable rule codes (`E0xx` errors, `W0xx` warnings), stack linting, `--catalog` to flag
  already-published versions, and `--strict`.
- SPDX 2.3 SBOM generated from the locked closure (conda and PyPI packages with checksums and purls,
  source and binary archives), embedded in recipe and stack images.
- `capsid stack build --no-cache` and `--engine`.
- Apptainer/Singularity tests run against local images (`docker-daemon:`) or registry images.
- `capsid submit` opens a real pull request through `gh` (fork, branch, push).
- JSON Schema for `recipe.yaml` and `stack.yaml` (`capsid schema`).
- Golden tests for every hub recipe and stack and one synthetic recipe per source build type.
- CI with ruff, mypy, actionlint; GitHub Actions pinned by SHA; Dependabot.
- Documentation: getting started, recipe and CLI references, image design, reproducibility,
  security, workflow integration, architecture, development and roadmap.

### Changed

- Conda environments are installed at their runtime path (`/opt/pixi/.pixi/envs/default`, linked as
  `/opt/conda-env`) so prefixes and script shebangs are correct. Images published before this change
  must be rebuilt.
- Generated `nextflow.config` uses `process.resourceLimits` (Nextflow 24.04+) and per-runtime
  profiles that can be combined with the `slurm` profile.
- `capsid compose` is now `capsid stack`; `compose` remains as a hidden, deprecated alias.
- OCI labels identify LaBiOmicS as vendor, the engine version and the hub repository as source.

### Fixed

- Recipe values (build arguments, Debian package names, `install_prefix`, `env` and `labels` keys,
  name, version, base image) can no longer inject Dockerfile instructions or shell commands; embedded
  files cannot terminate their heredoc early.
- All-digit `sha256` values written without quotes in YAML are reported clearly instead of failing
  validation obscurely.
- The weekly robot no longer overwrites or deletes existing recipes.
- `capsid import-bioconda` without `--version` failed.
- Build failures now show the last lines of the build log.

### Removed

- The experimental `minijinja` compatibility tests.

## [0.1.0]

- Initial release: recipe model, Dockerfile generation for `pixi-conda`, `uv-pypi` and `autotools`,
  lockfiles, build, test, exports, catalog and Capsid Hub integration.
