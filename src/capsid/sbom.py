"""
SPDX 2.3 software bill of materials embedded in every image (/etc/capsid/sbom.spdx.json).

The document describes what the recipe *declares* the image contains: the tool itself and, when a
lockfile exists, every locked conda artefact (with its URL and SHA-256) or PyPI distribution, and for
source/binary builds the verified upstream archives. Package URLs (purls) are included so scanners
can match the packages against vulnerability databases.

It is generated from the recipe alone (no image inspection), so it is identical for every build of
the same recipe. The hub's publish workflow additionally attests an SBOM scanned from the pushed image.
"""

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

import yaml

from capsid.models import SOURCE_BUILD_TYPES, BuildType, Recipe, StackRecipe
from capsid.project import HUB_URL, VENDOR, VERSION

NOASSERTION = "NOASSERTION"
# A single SPDX identifier or a simple AND/OR/WITH expression; anything else is NOASSERTION
_SPDX_EXPR = re.compile(r"[A-Za-z0-9.+-]+(\s+(AND|OR|WITH)\s+[A-Za-z0-9.+-]+)*")
_PYPI_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==(?P<version>[^\s;\\]+)")
_HASH = re.compile(r"--hash=sha256:(?P<sha>[0-9a-f]{64})")


def _spdx_id(*parts: str) -> str:
    return "SPDXRef-" + re.sub(r"[^A-Za-z0-9.-]+", "-", "-".join(parts)).strip("-")


def _license(value: str | None) -> str:
    value = (value or "").strip()
    return value if value and _SPDX_EXPR.fullmatch(value) else NOASSERTION


def _package(
    spdx_id: str,
    name: str,
    version: str,
    download: str | None,
    license_declared: str | None = None,
    sha256: str | None = None,
    purl: str | None = None,
    supplier: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    pkg: dict[str, Any] = {
        "SPDXID": spdx_id,
        "name": name,
        "versionInfo": version,
        "downloadLocation": download or NOASSERTION,
        "licenseConcluded": NOASSERTION,
        "licenseDeclared": _license(license_declared),
        "copyrightText": NOASSERTION,
        "filesAnalyzed": False,
    }
    if supplier:
        pkg["supplier"] = supplier
    if description:
        pkg["description"] = description
    if sha256:
        pkg["checksums"] = [{"algorithm": "SHA256", "checksumValue": sha256}]
    if purl:
        pkg["externalRefs"] = [
            {"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": purl}
        ]
    return pkg


def conda_lock_packages(pixi_lock: str) -> list[dict[str, Any]]:
    """One SPDX package per locked conda artefact (each platform's build is listed separately)."""
    data = yaml.safe_load(pixi_lock) or {}
    out: dict[str, dict[str, Any]] = {}
    for entry in data.get("packages") or []:
        url = entry.get("conda") if isinstance(entry, dict) else None
        if not url or url in out:
            continue
        path = urlparse(url).path.rstrip("/").split("/")
        filename, subdir = path[-1], path[-2] if len(path) >= 2 else ""
        channel = url.rsplit("/", 2)[0]
        stem = re.sub(r"(\.conda|\.tar\.bz2)$", "", filename)
        try:
            name, version, build = stem.rsplit("-", 2)
        except ValueError:
            continue
        purl = (
            f"pkg:conda/{quote(name)}@{quote(version)}"
            f"?build={quote(build)}&channel={quote(channel, safe='')}"
            f"&subdir={quote(subdir)}&type={filename[len(stem) + 1 :]}"
        )
        out[url] = _package(
            _spdx_id("Package", "conda", subdir, name, version, build),
            name,
            version,
            url,
            license_declared=entry.get("license"),
            sha256=entry.get("sha256"),
            purl=purl,
        )
    return list(out.values())


def pypi_lock_packages(requirements_lock: str) -> list[dict[str, Any]]:
    """One SPDX package per pinned distribution in a uv requirements.lock (first listed hash)."""
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in requirements_lock.splitlines():
        m = _PYPI_LINE.match(line)
        if m:
            name = re.sub(r"[-_.]+", "-", m.group("name")).lower()
            version = m.group("version")
            current = _package(
                _spdx_id("Package", "pypi", name, version),
                name,
                version,
                f"https://pypi.org/project/{name}/{version}/",
                purl=f"pkg:pypi/{quote(name)}@{quote(version)}",
            )
            out.append(current)
        h = _HASH.search(line)
        if h and current is not None and "checksums" not in current:
            current["checksums"] = [{"algorithm": "SHA256", "checksumValue": h.group("sha")}]
    return out


def _document(name: str, version: str, timestamp: int, main: dict[str, Any], deps: list[dict[str, Any]]):
    created = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    relationships = [
        {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": main["SPDXID"]}
    ]
    relationships += [
        {"spdxElementId": main["SPDXID"], "relationshipType": "CONTAINS", "relatedSpdxElement": d["SPDXID"]}
        for d in deps
    ]
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"capsid-sbom-{name}-{version}",
        # Deterministic (the document is embedded in a reproducible image) and unique per image
        "documentNamespace": f"{HUB_URL}/sbom/{name}/{version}",
        "creationInfo": {"created": created, "creators": [f"Tool: capsid-{VERSION}", f"Organization: {VENDOR}"]},
        "packages": [main, *deps],
        "relationships": relationships,
    }


def _recipe_inputs(recipe: Recipe) -> list[dict[str, Any]]:
    """Packages installed by a recipe: locked closure if available, else the declared inputs."""
    b = recipe.build
    deps: list[dict[str, Any]] = []
    if b.type == BuildType.PIXI_CONDA:
        if recipe.pixi_lock:
            return conda_lock_packages(recipe.pixi_lock)
        for spec in b.conda_packages or []:
            m = re.match(r"(?:[^:\s]+::)?([A-Za-z0-9_.-]+)\s*=*\s*([^\s,<>=!*|]*)", spec)
            if m:
                deps.append(
                    _package(
                        _spdx_id("Package", "conda", m.group(1)),
                        m.group(1),
                        m.group(2) or NOASSERTION,
                        None,
                        purl=f"pkg:conda/{quote(m.group(1))}" + (f"@{quote(m.group(2))}" if m.group(2) else ""),
                    )
                )
    elif b.type == BuildType.UV_PYPI:
        if recipe.requirements_lock:
            return pypi_lock_packages(recipe.requirements_lock)
        m = _PYPI_LINE.match(b.package or "")
        if m:
            deps.extend(pypi_lock_packages(f"{m.group('name')}=={m.group('version')}"))
    elif b.type in SOURCE_BUILD_TYPES and b.source:
        deps.append(
            _package(
                _spdx_id("Package", "source", recipe.name, recipe.version),
                f"{recipe.name}-source",
                recipe.version,
                b.source,
                license_declared=recipe.license,
                sha256=b.sha256,
            )
        )
    elif b.type == BuildType.BINARY:
        for arch in ("amd64", "arm64"):
            url, sha = getattr(b, f"binary_url_{arch}"), getattr(b, f"sha256_{arch}")
            if url:
                deps.append(
                    _package(
                        _spdx_id("Package", "binary", arch, recipe.name, recipe.version),
                        f"{recipe.name}-linux-{arch}",
                        recipe.version,
                        url,
                        license_declared=recipe.license,
                        sha256=sha,
                    )
                )
    return deps


def generate_spdx_sbom(recipe: Recipe) -> dict[str, Any]:
    """SPDX 2.3 JSON document for a recipe's image."""
    main = _package(
        _spdx_id("Package", recipe.name, recipe.version),
        recipe.name,
        recipe.version,
        recipe.homepage,
        license_declared=recipe.license,
        supplier=f"Organization: {VENDOR}",
        description=recipe.description,
    )
    return _document(recipe.name, recipe.version, recipe.build.source_date_epoch, main, _recipe_inputs(recipe))


def generate_stack_sbom(stack: StackRecipe, components: list[Recipe], source_date_epoch: int) -> dict[str, Any]:
    """SPDX 2.3 JSON document for a stack image: its components and the stack's locked closure."""
    main = _package(
        _spdx_id("Package", stack.name, stack.version),
        stack.name,
        stack.version,
        None,
        supplier=f"Organization: {VENDOR}",
        description=stack.description,
    )
    deps = [
        _package(
            _spdx_id("Package", "component", r.name, r.version),
            r.name,
            r.version,
            r.homepage,
            license_declared=r.license,
        )
        for r in components
    ]
    if stack.pixi_lock:
        deps += conda_lock_packages(stack.pixi_lock)
    if stack.requirements_lock:
        deps += pypi_lock_packages(stack.requirements_lock)
    return _document(stack.name, stack.version, source_date_epoch, main, deps)
