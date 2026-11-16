# Security policy

## Supported versions

Capsid is in active development. Security fixes are made on `main` and included in the next release;
the Capsid Hub pipeline is then updated to the fixed engine commit.

## Reporting a vulnerability

Please **do not** report vulnerabilities in public issues or pull requests.

Report them privately through GitHub:
[Security → Report a vulnerability](https://github.com/LaBiOmicS/capsid/security/advisories/new).
Include a description, the affected version or commit, steps to reproduce (a recipe that triggers the
problem is ideal) and the impact you expect.

We aim to acknowledge reports within 5 working days and to agree on a disclosure date with the
reporter once a fix is available.

## Scope

In scope:

- The `capsid` CLI and the Dockerfiles, exports and metadata it generates (for example a recipe value
  that injects commands, a download that is not verified, or a hardening measure that does not apply).
- The CI workflows of this repository and of [Capsid Hub](https://github.com/LaBiOmicS/capsid-hub)
  (for example a pull request or issue that can obtain write permissions or publish an image).

Out of scope:

- Vulnerabilities in packaged tools or their dependencies: report them upstream. Published images are
  rebuilt when fixed versions are available.
- Recipes that run arbitrary code in `build_commands` during a local build you started yourself: this
  is by design, as with any build script.

See [docs/security.md](docs/security.md) for the security model and how to verify published images.
