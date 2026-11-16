# CLI reference

```
capsid [COMMAND] [ARGS] [OPTIONS]
```

Every command accepts `--help`. Commands that take a recipe accept either a path to a `recipe.yaml`
or a target name: `samtools@1.25` (a recipe found in the [search path](#recipe-search-path)), or an
upstream reference such as `bioconda:samtools@1.25` or `pypi:cutadapt` (scaffolded on the fly into
`recipes/`).

| Command | Purpose |
| :--- | :--- |
| [`init`](#capsid-init) | Generate a recipe from PyPI, Bioconda or a GitHub release |
| [`lock`](#capsid-lock) | Write the lockfile of a recipe or stack |
| [`lint`](#capsid-lint) | Check recipes and stacks |
| [`dockerfile`](#capsid-dockerfile) | Print or save the generated Dockerfile |
| [`build`](#capsid-build) | Build, test and optionally push an image |
| [`test`](#capsid-test) | Run a recipe's test against an existing image |
| [`stack`](#capsid-stack) | Dockerfile, build and test for multi-tool stacks |
| [`export`](#capsid-export) | Nextflow, Snakemake, Apptainer and SBOM exports |
| [`schema`](#capsid-schema) | Write the JSON Schema of the recipe formats |
| [`catalog`](#capsid-catalog), [`docs`](#capsid-docs) | Build the hub catalog and its pages |
| [`submit`](#capsid-submit) | Send a recipe to Capsid Hub |
| [`sync`](#capsid-sync) | Download hub recipes that are missing locally |
| [`import-bioconda`](#capsid-import-bioconda) | Shortcut for `init bioconda:<package>` |
| [`bot`](#capsid-bot) | Upstream discovery robot used by the hub |

`capsid --version` (or `-V`) prints the engine version; include it in bug reports.

Exit codes: `0` success, `1` error (invalid input, failed build, lint errors), `2` the image was built
but its test failed.

---

## `capsid init`

```
capsid init TARGET [--version/-v VERSION] [--out/-o PATH] [--dry-run]
```

Inspects an upstream package and writes `recipes/<name>/<version>/recipe.yaml`.

| `TARGET` | Result |
| :--- | :--- |
| `bioconda:<package>[@<version>]` | `pixi-conda` recipe from the Bioconda recipe metadata. Python packages that also exist on PyPI become `uv-pypi`. |
| `pypi:<package>[@<version>]` | `uv-pypi` recipe from PyPI metadata (license taken from `license_expression` or the classifiers). |
| `github:<owner>/<repo>[@<version>]` or a GitHub URL | Source recipe for the given release (or the latest): the build type is detected from the repository (`CMakeLists.txt`, `configure.ac`, `Cargo.toml`, `Makefile`), and the source archive's SHA-256 is computed. |
| `<name>` | Tries PyPI, then Bioconda, then GitHub (for `owner/repo`). |

| Option | Description |
| :--- | :--- |
| `--version`, `-v` | Version to scaffold (alternative to `@<version>`). |
| `--out`, `-o` | Output file. |
| `--dry-run` | Print the YAML instead of writing it. |

Generated recipes are a starting point: review the description, license, dependencies and, above all,
the test command.

---

## `capsid lock`

```
capsid lock DEFINITION
```

Resolves the dependency closure of a `recipe.yaml` or stack `.yaml` inside the digest-pinned pixi/uv
images used by the build, and writes `pixi.lock` and/or `requirements.lock` next to it (stacks:
`<name>.pixi.lock`, `<name>.requirements.lock`). Requires Docker or Podman. Source and binary recipes
have nothing to lock. See [Reproducibility](reproducibility.md#lockfiles).

---

## `capsid lint`

```
capsid lint [TARGET] [--catalog/-c catalog.json] [--strict]
```

Lints a `recipe.yaml`, a stack `.yaml`, or every recipe and stack under a directory (default:
`recipes`). Exits with `1` when any error is found.

| Option | Description |
| :--- | :--- |
| `--catalog`, `-c` | The hub's `catalog.json`; reports versions that are already published (`W040`). |
| `--strict` | Treat warnings as errors. |

The rules are listed in [Security › Recipe linter](security.md#recipe-linter).

---

## `capsid dockerfile`

```
capsid dockerfile TARGET [--out/-o PATH]
```

Prints the generated Dockerfile with syntax highlighting, or writes it to a file. Stacks are accepted
too. The Dockerfile is self-contained (lockfiles and metadata are embedded as heredocs) and can be
built with `docker buildx build -f Dockerfile .`.

---

## `capsid build`

```
capsid build TARGET [--tag/-t TAG] [--engine/-e ENGINE] [--no-cache]
                    [--apptainer/--sif/-a] [--test/--no-test] [--push/-p]
```

1. Renders the Dockerfile into `.capsid-build/<name>/`.
2. Builds the image for the host architecture with the selected engine.
3. Runs the recipe's test (unless `--no-test`); a failing test exits with `2`.
4. With `--apptainer`, converts the image to `<name>-<version>.sif`.
5. With `--push`, rebuilds for every platform of the recipe and pushes a multi-architecture image.

| Option | Default | Description |
| :--- | :--- | :--- |
| `--tag`, `-t` | `<name>:<version>` | Image reference. Use a registry reference with `--push`. |
| `--engine`, `-e` | first found of `docker`, `podman`, `nerdctl` | Build engine. |
| `--no-cache` | off | Build without the build cache. |
| `--apptainer`, `--sif`, `-a` | off | Also write a SIF file. Without Apptainer/Singularity installed, an OCI archive (`.oci.tar`) is saved instead, with the command to convert it elsewhere. |
| `--test/--no-test` | on | Run the test after the build. |
| `--push`, `-p` | off | Publish after the test passed (see [Publishing](reproducibility.md#publishing)). |

A stack file is accepted and handled like `capsid stack build`.

---

## `capsid test`

```
capsid test TARGET [--image/-i IMAGE] [--engine/-e ENGINE] [--sif/-s FILE]
```

Runs the recipe's (or stack's) test against an image that already exists, for example a published
one.

| Option | Default | Description |
| :--- | :--- | :--- |
| `--image`, `-i` | `<name>:<version>` | Image reference, tag or digest. |
| `--engine`, `-e` | detected | `docker`, `podman`, `apptainer` or `singularity`. With a SIF file present, Apptainer/Singularity is preferred. |
| `--sif`, `-s` | `<name>-<version>.sif` if present | SIF file for Apptainer/Singularity. Without one, the image itself is used: `docker://<image>` for a registry reference, `docker-daemon:<image>` for a local tag. |

---

## `capsid stack`

```
capsid stack dockerfile STACK [--out/-o PATH]
capsid stack build STACK [--tag/-t TAG] [--engine/-e ENGINE] [--no-cache]
                         [--apptainer/--sif/-a] [--test/--no-test] [--push/-p]
capsid stack test STACK [--image/-i IMAGE] [--engine/-e ENGINE] [--sif/-s FILE]
```

Same behaviour and options as `dockerfile`, `build` and `test`, for a stack definition. The default
tag is `<stack>:<version>`. The image is published for the platforms all components share.
`capsid compose` is a deprecated alias of `capsid stack`.

---

## `capsid export`

```
capsid export nextflow  TARGET [--registry/-r PREFIX] [--out/-o FILE]
capsid export snakemake TARGET [--registry/-r PREFIX] [--out/-o FILE]
capsid export config  [--target/-t TARGET] [--registry/-r PREFIX] [--out/-o FILE]
capsid export def       TARGET [--out/-o FILE]
capsid export sbom      TARGET [--out/-o FILE]
```

| Subcommand | Output |
| :--- | :--- |
| `nextflow` | A DSL2 `process` with a `container` directive pointing to `<registry>/<name>:<version>`. |
| `snakemake` | A rule with a `container:` directive (`docker://<registry>/<name>:<version>`). |
| `config` | A `nextflow.config` with `docker`, `podman`, `singularity`, `apptainer` and `slurm` profiles; with `--target`, a `withName` block selecting the tool's image. |
| `def` | An Apptainer/Singularity definition file bootstrapping from the published image. |
| `sbom` | The SPDX 2.3 SBOM that is embedded in the image. |

`--registry` defaults to `ghcr.io/labiomics/capsid`. See [Workflow integration](workflow-integration.md).

---

## `capsid schema`

```
capsid schema [--out-dir/-o DIR] [--check]
```

Writes `recipe.schema.json` and `stack.schema.json` to `DIR` (default `schema`). With `--check`,
writes nothing and exits with `1` if the files are missing or out of date (used by the hub CI).

---

## `capsid catalog`

```
capsid catalog [--dir/-d recipes] [--out/-o catalog.json]
```

Validates every recipe under `--dir` and writes `catalog.json`: for each tool and version, the build
type, platforms, license, image references (`docker://` and `apptainer` URIs) and Nextflow/Snakemake
snippets.

## `capsid docs`

```
capsid docs [--dir/-d recipes] [--catalog/-c catalog.json] [--out-dir/-o .]
```

Generates `CATALOG.md` and a searchable `catalog.html` page from `catalog.json`.

---

## `capsid submit`

```
capsid submit TARGET [--upstream/-u OWNER/REPO] [--web/-w]
```

Lints the recipe (errors abort the submission), then:

- with the GitHub CLI (`gh`) authenticated and inside a git clone: forks the hub if needed, commits
  the recipe and its lockfile on a new branch `recipe-<name>-<version>`, pushes it to the fork and
  opens a pull request;
- otherwise, or with `--web`: prints a link to a pre-filled issue on the hub. The hub turns the issue
  into a pull request after building and testing the recipe.

`--upstream` defaults to `LaBiOmicS/capsid-hub`.

## `capsid sync`

```
capsid sync [--upstream/-u OWNER/REPO] [--dir/-d recipes] [--catalog/-c catalog.json]
```

Reads the hub's `catalog.json`, downloads every recipe (and lockfile) that is missing locally, and
regenerates the local catalog. Existing files are never overwritten.

## `capsid import-bioconda`

```
capsid import-bioconda PACKAGE [--version/-v VERSION] [--out/-o PATH]
```

Equivalent to `capsid init bioconda:PACKAGE`.

---

## `capsid bot`

The robot behind the hub's weekly update workflow. It reads Bioconda's channel data and recent
commits, compares them with the local `recipes/` tree, and generates recipes for new tools and new
versions.

```
capsid bot check [--days/-d 7] [--limit/-l 15] [--tools/-t a,b] [--recipes-dir/-r recipes]
capsid bot sync  [--days/-d 7] [--limit/-l 10] [--tools/-t a,b] [--recipes-dir/-r recipes]
                 [--catalog/-c catalog.json] [--dry-run] [--report FILE]
capsid bot migrate-all [--category/-c core|r|perl|all] [--limit/-l N]
                       [--recipes-dir/-r recipes] [--dry-run] [--update-catalog]
```

| Subcommand | Description |
| :--- | :--- |
| `check` | Lists candidates; writes nothing. |
| `sync` | Writes recipes for the candidates, lints them, updates the catalog and optionally writes a Markdown report (used as the pull request body). |
| `migrate-all` | Bulk conversion of Bioconda packages. Generated recipes have no lockfile and must be reviewed. |

---

## Recipe search path

Targets written as `name` or `name@version`, and stack components, are looked up in:

1. each directory in `$CAPSID_RECIPES_PATH` (separated by `:`);
2. `./recipes`;
3. `./capsid-hub/recipes`;
4. `../capsid-hub/recipes`.

Without a version, the highest version (natural ordering: `1.9 < 1.25`) is used.

## Environment variables

| Variable | Used by | Effect |
| :--- | :--- | :--- |
| `CAPSID_RECIPES_PATH` | target resolution, stacks, lint | Additional recipe trees, searched first. |
| `DOCKER_BUILDKIT` | `build` | Set to `1` automatically for Docker. |
| `SOURCE_DATE_EPOCH` | `build --push` | Set from the recipe; do not override. |
