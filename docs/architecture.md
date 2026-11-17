# Architecture

Capsid is a Python 3.12 package (`src/capsid/`) with a [Typer](https://typer.tiangolo.com) CLI.
It does not build images itself: it validates recipes, renders Dockerfiles and exports from
templates, and drives Docker/Podman/Apptainer through their command lines.

- [Data flow](#data-flow)
- [Modules](#modules)
- [Templates](#templates)
- [Design rules](#design-rules)
- [Tests](#tests)
- [Capsid Hub](#capsid-hub)

---

## Data flow

```
                  ┌──────────── capsid init (scaffolders: PyPI, Bioconda, GitHub)
                  ▼
recipe.yaml ──► models.Recipe (pydantic) ──► linter ──► capsid lint
     │                 │
     │                 ├──► locking ──► pixi/uv in pinned containers ──► pixi.lock / requirements.lock
     │                 │                                                        │
     │                 ▼                                                        │
     │        builders.dockerfile_gen / stack_builder ◄─────────────────────────┘
     │          │  templates/dockerfile/*.j2
     │          │  + sbom, ro_crate, exporter (embedded metadata)
     │          ▼
     │      Dockerfile ──► builders.runner ──► docker/podman/nerdctl build ──► image
     │                                                                          │
     │                               tester (docker/podman/apptainer) ◄─────────┤
     │                                                                          ▼
     │                                            runner.publish_image (multi-arch push)
     ▼
catalog ──► catalog.json ──► docs_gen ──► CATALOG.md
         recipes + lockfiles ──► site ──► hub website (GitHub Pages)
```

## Modules

| Module | Responsibility |
| :--- | :--- |
| `main.py` | CLI commands and argument handling; resolves targets to recipes. |
| `models.py` | Pydantic models for recipes and stacks, validators, `recipe_to_yaml`. The single definition of the recipe format. |
| `schema.py` | JSON Schema generation from the models (`capsid schema`). |
| `project.py` | Repository URLs, registry, vendor and engine version. |
| `resolver.py` | Finds `name[@version]` recipes in the search path. |
| `linter.py` | Recipe and stack rules (`capsid lint`). |
| `locking.py` | `capsid lock`: renders the pixi manifest / uv input and runs the solvers in containers. |
| `builders/dockerfile_gen.py` | Dockerfile for one recipe; pinned images; apt snapshot; build-type logic. |
| `builders/stack_builder.py` | Dockerfile for a stack; merges components into shared environments. |
| `builders/runner.py` | Runs builds, SIF conversion and multi-architecture publication. |
| `tester.py` | Runs a recipe's test in Docker, Podman, Apptainer or Singularity and checks the result. |
| `exporter.py` | Nextflow module and config, Snakemake rule, Apptainer definition. |
| `sbom.py`, `ro_crate.py` | Embedded SPDX SBOM and RO-Crate metadata. |
| `templating.py` | Jinja2 environment and filters (`dq`, `shq`, `heredoc`). |
| `scaffolders/` | `capsid init` for PyPI, Bioconda and GitHub. |
| `catalog.py`, `docs_gen.py` | `catalog.json` and `CATALOG.md` for the hub. |
| `site.py`, `templates/site/` | The hub website (`capsid site`). |
| `publisher.py`, `syncer.py` | `capsid submit` and `capsid sync`. |
| `bot.py` | The upstream robot (`capsid bot`). |

## Templates

All generated text lives in `src/capsid/templates/` as Jinja2 files, so the output can be read and
reviewed as a file rather than assembled from Python strings:

| Template | Output |
| :--- | :--- |
| `dockerfile/pixi_builder.j2` | Conda builder stage |
| `dockerfile/uv_builder.j2` | Python builder stage |
| `dockerfile/source_builder.j2` | Source and binary builder stage |
| `dockerfile/apt_snapshot.j2` | apt sources pinned to snapshot.debian.org |
| `dockerfile/runtime.j2` | The shared hardened runtime stage |
| `exports/nextflow_process.nf.j2`, `exports/nextflow_config.j2` | Nextflow |
| `exports/snakemake_rule.smk.j2` | Snakemake |
| `exports/apptainer.def.j2` | Apptainer definition file |

The environment uses `StrictUndefined` (a missing variable is an error), no autoescaping, and three
filters: `dq` for double-quoted Dockerfile strings, `shq` for shell words, and `heredoc` for embedded
files.

## Design rules

- **Deterministic output.** Rendering depends only on the recipe, its lockfile and the Capsid version:
  no current time, no host information, sorted keys. Timestamps come from `SOURCE_DATE_EPOCH`.
- **One runtime stage.** Every build type and stacks share `runtime.j2`, so hardening changes apply
  everywhere at once.
- **Pin everything that is downloaded**, and verify it in the Dockerfile itself, not in Python.
- **Recipe values never become code.** They are quoted, validated against strict patterns, or
  embedded as data (see [Security](security.md#build-time-integrity)).
- **The model is the contract.** The CLI, the linter, the JSON Schema and the hub all derive from
  `models.py`.

## Tests

| Suite | What it checks |
| :--- | :--- |
| `tests/test_*.py` | Unit tests for models, linter, generators, locking, tester, SBOM, publishing commands, CLI. |
| `tests/test_golden.py` | **Golden tests**: the exact Dockerfile, Nextflow module and config, Snakemake rule and Apptainer definition for every hub recipe and stack (`tests/golden/inputs/`) and one synthetic recipe per source build type (`tests/golden/synthetic/`), plus the catalog and JSON Schemas, compared byte for byte with `tests/golden/expected/`. |
| `tests/smoke/` | Recipes used by CI for real locked builds (pixi and uv). |

Any change to generated output appears as a diff in `tests/golden/expected/`, which is reviewed with
the code change. See [Development](development.md#golden-files).

## Capsid Hub

[Capsid Hub](https://github.com/LaBiOmicS/capsid-hub) is a separate repository that contains only data
(recipes, stacks, lockfiles, the catalog and the JSON Schemas) and GitHub Actions workflows. The
workflows install this package from a fixed commit (`CAPSID_REF`) and call the CLI; there is no other
hub-specific code.
