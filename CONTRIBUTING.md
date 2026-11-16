# Contributing to Capsid

Thank you for helping improve Capsid. This repository contains the engine (the `capsid` CLI). To
package a tool, contribute a recipe to [Capsid Hub](https://github.com/LaBiOmicS/capsid-hub) instead.

## Reporting bugs and proposing changes

- **Bugs:** open an issue with the Capsid version (`capsid --version`), the command, the recipe (or a
  minimal one that reproduces the problem) and the full output.
- **Features and format changes:** open an issue first to discuss the design, especially for changes
  to the recipe format, which affect every recipe in the hub.
- **Security vulnerabilities:** do not open a public issue; follow [SECURITY.md](SECURITY.md).

## Making a change

1. Fork the repository and create a branch from `main`.
2. Set up the environment and make your change:
   ```bash
   uv sync --group dev
   ```
3. Add or update tests. Changes to generated output must come with regenerated golden files
   (`uv run python scripts/update_golden.py`), and the diff in `tests/golden/expected/` must be
   reviewed as part of the change.
4. Run the checks that CI runs:
   ```bash
   uvx ruff check src tests scripts
   uvx ruff format --check src tests scripts
   uv run --group dev mypy
   uv run pytest -q
   uv run python scripts/update_golden.py --check
   ```
5. For changes to templates, pinned images or build logic, also run at least one real build per
   affected build type (see [docs/development.md](docs/development.md#checks)).
6. Update the documentation in `docs/` and add an entry under "Unreleased" in
   [CHANGELOG.md](CHANGELOG.md).
7. Open a pull request describing what changed and why, and how you tested it.

## Guidelines

- Generated output must stay deterministic: no timestamps, host information or unordered collections.
- Every download in a generated Dockerfile must be pinned and verified.
- Recipe values must never be interpolated into shell or Dockerfile syntax without quoting or
  validation (see [docs/security.md](docs/security.md#build-time-integrity)).
- Keep the recipe format backwards-compatible where possible; hub recipes must keep validating.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`).

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
