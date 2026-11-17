# Security

This page covers how Capsid images are hardened, how recipes are checked, and how published images
can be verified. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

- [Image hardening](#image-hardening)
- [Build-time integrity](#build-time-integrity)
- [Recipe linter](#recipe-linter)
- [Capsid Hub pipeline](#capsid-hub-pipeline)
- [Verifying a published image](#verifying-a-published-image)
- [Scanning for vulnerabilities](#scanning-for-vulnerabilities)

---

## Image hardening

| Measure | Effect |
| :--- | :--- |
| Non-root user `bio` (`USER 1000:1000`) | Processes in the container have no root privileges by default; numeric UID satisfies Kubernetes `runAsNonRoot`. |
| Root-owned, read-only tool prefixes | A compromised or buggy tool cannot modify its own installation. |
| Multi-stage build | Compilers, package managers, solvers and download tools are absent from the runtime image. |
| Minimal runtime packages | `tini`, `bash`, `ca-certificates` and the recipe's `dependencies.run`, installed with `--no-install-recommends`. |
| `tini` as PID 1 | Correct signal forwarding and zombie reaping. |
| No secrets in images | Builds use no credentials; downloads are public URLs verified by checksum. |
| Embedded SBOM | `/etc/capsid/sbom.spdx.json` lists every locked package with its checksum and purl. |

Run images with the usual additional restrictions where your platform allows it, for example
`docker run --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp ...`.

## Build-time integrity

- **Everything downloaded is verified.** Base and builder images by digest; conda artefacts by the
  SHA-256 in `pixi.lock`; PyPI distributions by `--require-hashes`; source archives and binaries by
  the recipe's `sha256` fields (`sha256sum -c` runs before unpacking); Debian packages by Debian's
  signed repository metadata.
- **No unverified download path is generated.** A source or binary recipe without a checksum is a lint
  error, and the generator refuses to render it.
- **Recipe values cannot inject instructions.** Values that reach shell commands (URLs, package specs,
  `configure_args`, `cmake_args`, `cargo_args`, …) are shell-quoted one argument at a time. Values
  written unquoted (name, version, Debian package names, `install_prefix`, `env` and `labels` keys,
  `base_image`) must match strict patterns. Lockfiles and metadata are embedded through quoted
  heredocs, and content that contains its own heredoc terminator is refused.
- **`build_commands` are the exception**: they are shell code by definition. They are reviewed by a
  maintainer and checked by the linter for known dangerous patterns (below).

## Recipe linter

`capsid lint` checks recipes and stacks before anything is built. **Errors** fail the command (and
the hub CI); **warnings** are reported but do not fail it, unless `--strict` is given.

### Errors

| Rule | Meaning |
| :--- | :--- |
| `E001_FILE_NOT_FOUND` | The file does not exist. |
| `E002_SCHEMA_INVALID` | Not valid YAML, or does not match the recipe/stack model (missing field, wrong type, invalid checksum format, …). |
| `E003_NAME_PATH_MISMATCH` | `name` differs from the directory (`recipes/<name>/…`) or from the stack file name. |
| `E004_VERSION_PATH_MISMATCH` | `version` differs from the version directory. |
| `E005_INVALID_NAME` | Name is not lowercase `[a-z0-9][a-z0-9._-]*` (it becomes an image name). |
| `E006_INVALID_VERSION` | Version is not a valid image tag. |
| `E007_NO_PLATFORMS` | `platforms` is empty. |
| `E010_DANGEROUS_COMMAND` | A `build_commands` entry pipes a download into a shell (`curl … \| sh`), uses `sudo` or `eval`, makes files world-writable (`chmod 777`), deletes `/`, or disables TLS verification (`curl -k`, `wget --no-check-certificate`). |
| `E011_MISSING_SHA256` | A source archive or binary has no checksum. |
| `E012_MISSING_BUILD_INPUT` | The build type's required input is missing (`package`, `conda_packages`, `source`, or a binary URL for a listed platform). |
| `E013_UNSUPPORTED_PYTHON` | `python_version` has no pinned Python image and no `base_image` is given. |
| `E015_CHECKSUM_WITHOUT_OUTPUT` | `test.expected_checksum` without `test.output_file`. |
| `E016_STALE_LOCKFILE` | A pinned package version is not in the lockfile (the locked build would fail or install something else). |
| `E017_LOCKFILE_PLATFORM` | `pixi.lock` lacks an environment for a platform in `platforms`. |
| `E030_DUMMY_TEST` | The test command is empty or trivial (`true`, `exit 0`, `echo`, …). |
| `E050_EMPTY_STACK` | A stack without components. |
| `E051_COMPONENT_NOT_FOUND` | A stack component recipe cannot be found or is invalid. |
| `E052_UNSUPPORTED_COMPONENT` | A stack component is not `pixi-conda` or `uv-pypi`. |
| `E053_NO_COMMON_PLATFORM` | Stack components share no platform. |
| `E054_DUPLICATE_COMPONENT` | The same component is listed twice. |
| `E055_INCOMPATIBLE_COMPONENTS` | Components cannot share one environment (e.g. different Python versions). |

### Warnings

| Rule | Meaning |
| :--- | :--- |
| `W010_DOWNLOAD_IN_BUILD_COMMAND` | A build command downloads something that is not checksum-verified. |
| `W011_DUPLICATE_PLATFORM` | A platform is listed twice. |
| `W012_UNPINNED_PACKAGE` | A conda or PyPI package is not pinned with `==`. |
| `W013_INSECURE_HTTP` | A source or binary URL uses plain HTTP. |
| `W014_MISSING_LOCKFILE` | No lockfile; transitive dependencies resolve at build time. |
| `W017_UNPINNED_BASE_IMAGE` | `base_image` is not pinned by `@sha256:` digest. |
| `W018_UNKNOWN_DEPENDENCY_GROUP` | `dependencies` has a group other than `build` and `run`. |
| `W019_IGNORED_FIELD` | A field has no effect for this build type (e.g. `cmake_args` in a `pixi-conda` recipe, `apt_snapshot` with `base_image`). |
| `W020_MISSING_LICENSE` | No license. |
| `W021_SHORT_DESCRIPTION` | Description missing or shorter than 10 characters. |
| `W022_INVALID_LICENSE` | License is not an SPDX identifier or expression. |
| `W023_MISSING_HOMEPAGE` | No homepage. |
| `W040_PUBLISHED_VERSION` | This name and version is already in the catalog; merging republishes the same tag. |
| `W041_CATALOG_UNREADABLE` | The catalog given with `--catalog` cannot be read. |
| `W051_UNPINNED_COMPONENT` | A stack component has no version. |

Run the linter on everything:

```bash
capsid lint recipes/                      # recipes
capsid lint .                             # recipes and stacks
capsid lint . --catalog catalog.json      # plus W040 for already-published versions
```

The linter is a safety net, not a sandbox. The hub's real protection against a malicious recipe is
that untrusted recipes are only built in jobs without write permissions, and that publication happens
after human review and merge.

## Capsid Hub pipeline

| Workflow | Trigger | Token permissions | What runs |
| :--- | :--- | :--- | :--- |
| `pr-audit` | pull request | `contents: read` | Schema check, lint, build and test of the changed recipes and stacks |
| `issue-submission` | new issue | extract and build: `contents: read`; open PR: `contents: write`, `pull-requests: write` | The recipe from the issue is parsed and built **without** write permissions; only if that succeeds does a separate job, which never executes the recipe, open a pull request |
| `publish-release` | push to `main` (or manual) | publish job: `packages: write`, `id-token: write`, `attestations: write` | Build, test, push, sign, attest; then pull and test on native amd64 and arm64 |
| `friday-robot-sync` | weekly schedule (or manual) | `contents: write`, `pull-requests: write` | Generates recipes from Bioconda updates and opens a pull request for review |

Further measures:

- Workflows install the Capsid engine from a fixed commit (`CAPSID_REF`), not from a branch.
- Every GitHub Action is pinned by commit SHA; Dependabot proposes updates.
- Values from issues and pull requests reach shell steps only through environment variables, and file
  paths are validated against a strict pattern before use.
- Images are signed by digest, never by tag.

## Verifying a published image

Images are signed with [Sigstore Cosign](https://docs.sigstore.dev/) using keyless signing: the
certificate is issued to the hub's publish workflow through GitHub's OIDC provider and recorded in the
public Rekor transparency log.

```bash
IMAGE=ghcr.io/labiomics/capsid/samtools:1.25

# Signature: made by the publish workflow of LaBiOmicS/capsid-hub
cosign verify "$IMAGE" \
  --certificate-identity-regexp '^https://github.com/LaBiOmicS/capsid-hub/\.github/workflows/publish-release\.yaml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# SBOM attestation (SPDX, scanned from the pushed image).
# The hub signs with Cosign 2; Cosign 3 needs --new-bundle-format=false to find this attestation.
cosign verify-attestation --new-bundle-format=false --type spdxjson "$IMAGE" \
  --certificate-identity-regexp '^https://github.com/LaBiOmicS/capsid-hub/\.github/workflows/publish-release\.yaml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  | jq -r .payload | base64 -d | jq '.predicate.packages | length'

# SLSA build provenance
gh attestation verify "oci://$IMAGE" --repo LaBiOmicS/capsid-hub
```

For pipelines, reference images by digest (`…/samtools@sha256:…`) after verifying them once; a tag
can be re-pointed by a republish, a digest cannot.

Kubernetes clusters can enforce these signatures at admission time with the
[Sigstore policy-controller](https://docs.sigstore.dev/policy-controller/overview/) or Kyverno, using
the same identity and issuer.

## Scanning for vulnerabilities

The embedded and attested SBOMs can be scanned without pulling the full image:

```bash
docker run --rm ghcr.io/labiomics/capsid/samtools:1.25 cat /etc/capsid/sbom.spdx.json > sbom.json
grype sbom:sbom.json
# or scan the image directly
trivy image ghcr.io/labiomics/capsid/samtools:1.25
```

Fixes reach images in two ways: a new recipe version (new tool or dependency versions), or a
republish of an existing version after Capsid's weekly pin update (newer base images and apt
snapshot).
