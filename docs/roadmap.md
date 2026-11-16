# Roadmap

Planned work, roughly in priority order. Items move to the [changelog](../CHANGELOG.md) when they
ship. Suggestions are welcome as GitHub issues.

## Near term

- **Conda activation variables.** Capture the variables set by `etc/conda/activate.d` scripts at build
  time (`pixi shell-hook`) and set them in the image, so packages such as Java- or GDAL-based tools
  work without `build.env`.
- **Lockfiles for robot recipes.** Have the weekly robot run `capsid lock` for the recipes it
  generates, so they arrive in pull requests fully pinned.
- **Native arm64 publishing.** Build each architecture on a native runner and merge the manifests,
  instead of building arm64 under QEMU emulation.
- **Patches for source builds.** A `patches` field with checksum-verified patch files applied before
  the build.
- **Richer test definitions.** Several test commands per recipe, and tests that run without network
  access (`--network none`).

## Medium term

- **Stacks with source and binary components**, installed side by side in `/opt/<name>`.
- **A Python API** (`capsid.api`) for tools that want to generate or validate recipes without the CLI.
- **Vulnerability reports in the hub catalog**, from scanning the attested SBOMs on a schedule.
- **Mirroring support**: a documented `--registry` rewrite for the whole catalog, for sites that copy
  images to an internal registry.

## Under consideration

- Additional runtime bases (for example a distroless runtime for statically linked binaries).
- GA4GH Tool Registry Service (TRS) endpoints generated from the catalog.
- A Bioconductor/CRAN build type based on `r-` conda packages with curated test commands.
