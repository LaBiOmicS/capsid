# Development

- [Setup](#setup)
- [Checks](#checks)
- [Golden files](#golden-files)
- [Changing the recipe format](#changing-the-recipe-format)
- [Adding a build type](#adding-a-build-type)
- [Updating pinned images](#updating-pinned-images)
- [Testing against the hub](#testing-against-the-hub)
- [Releasing](#releasing)

---

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker with buildx (for build tests),
optionally Apptainer.

```bash
git clone https://github.com/LaBiOmicS/capsid.git
cd capsid
uv sync --group dev
uv run capsid --help
```

## Checks

These run in CI on every push and pull request (`.github/workflows/ci.yaml`) and must pass before
merging:

```bash
uvx ruff check src tests scripts          # lint (rules in pyproject.toml)
uvx ruff format --check src tests scripts # formatting
uv run --group dev mypy                   # type check
uv run pytest -q                          # unit and golden tests
uv run python scripts/update_golden.py --check
```

`uvx ruff format src tests scripts` and `uvx ruff check --fix src tests scripts` fix most findings.

The unit tests do not need Docker. Changes to templates, base images or build logic should also be
checked with real builds:

```bash
uv run capsid build tests/smoke/seqkit/recipe.yaml
uv run capsid build tests/smoke/cutadapt/recipe.yaml
uv run capsid build tests/golden/synthetic/recipes/hello/2.12.1/recipe.yaml   # autotools
```

`tests/golden/synthetic/recipes/` contains one verified recipe per source build type (`autotools`,
`make`, `cmake`, `rust-cargo`, `binary`).

## Golden files

`tests/golden/expected/` holds the exact output for every input in `tests/golden/inputs/` (a copy of
the hub's recipes, stacks and lockfiles) and `tests/golden/synthetic/`: Dockerfiles, Nextflow modules
and configs, Snakemake rules, Apptainer definitions, the catalog and the JSON Schemas.

When a change intentionally modifies generated output:

```bash
uv run python scripts/update_golden.py              # regenerate expected files
git diff tests/golden/expected                       # review every change
```

Commit the regenerated files with the code change; reviewers see the effect on real recipes in the
diff. To refresh the inputs from a local hub checkout:

```bash
uv run python scripts/update_golden.py --hub ../capsid-hub
```

## Changing the recipe format

The format is defined once, in `src/capsid/models.py`. When adding or changing a field:

1. Update the model, with a `description` (it becomes the JSON Schema documentation).
2. Use it in the generator, linter (including `TYPE_SPECIFIC_FIELDS` for `W019`) and SBOM/RO-Crate if
   relevant.
3. Regenerate golden files; the schema files are part of them.
4. Document it in [docs/recipe-reference.md](recipe-reference.md) and the changelog.
5. After release, run `capsid schema` in the hub so editors pick up the change.

Prefer backwards-compatible changes: existing recipes in the hub must keep validating.

## Adding a build type

1. Add the value to `BuildType` (and to `SOURCE_BUILD_TYPES` if it builds from a downloaded archive).
2. Add its toolchain to `SOURCE_TOOLCHAINS` and its default steps to `templates/dockerfile/source_builder.j2`,
   or a new builder template.
3. Extend the linter's required-input checks (`E011`, `E012`) and `TYPE_SPECIFIC_FIELDS`.
4. Add a synthetic recipe under `tests/golden/synthetic/recipes/`, verify it with a real
   `capsid build`, and regenerate the golden files.
5. Document it in the recipe reference.

## Updating pinned images

```bash
uv run python scripts/bump_pins.py --check   # what is outdated
uv run python scripts/bump_pins.py           # refresh digests (same tags) and the apt snapshot
```

The weekly `bump-pins` workflow does this automatically and opens a pull request after smoke builds.
To move to a new **tag** (a new Python, pixi, uv or Rust version), edit the constant in
`src/capsid/builders/dockerfile_gen.py` by hand with the new `tag@sha256:digest`
(`docker buildx imagetools inspect <image>:<tag>` prints the digest), then regenerate the golden files
and run the smoke builds.

## Testing against the hub

The hub's workflows install Capsid from the commit in their `CAPSID_REF` variable. To try an engine
change against all hub recipes before that:

```bash
cd ../capsid-hub
uv run --project ../capsid capsid lint .
uv run --project ../capsid capsid build recipes/samtools/1.25/recipe.yaml
```

After the change is merged, update `CAPSID_REF` in the hub's four workflows to the new commit SHA in
one pull request (see the hub's [maintainer guide](https://github.com/LaBiOmicS/capsid-hub/blob/main/docs/maintainers.md)).

## Releasing

1. Update `version` in `pyproject.toml` **and** `VERSION` in `src/capsid/project.py` (a test checks
   they match).
2. Move the "Unreleased" entries in `CHANGELOG.md` under the new version.
3. Tag the commit: `git tag -s v0.2.0 && git push --tags`.
4. Update `CAPSID_REF` in the hub.

Note that a new engine version changes generated Dockerfiles (labels, pins), so republished images get
new digests; see [Reproducibility](reproducibility.md#updating-pins).
