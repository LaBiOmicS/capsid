"""
Static website for a Capsid hub.

`build_site` reads a hub checkout (recipes/, stacks/ and their lockfiles) and writes a
self-contained multi-page site: a presentation page, a paginated catalogue, one page per tool,
one page per published image (tool version or stack) with its locked dependency closure, usage and
verification instructions, and a copy of catalog.json. Links are relative and point at
index.html files, so the output works on GitHub Pages and when opened from disk.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from capsid.catalog import build_catalog_dict
from capsid.locking import attach_lockfiles, lock_paths
from capsid.models import Recipe, StackRecipe
from capsid.project import ENGINE_URL, HUB_REPO, HUB_URL, REGISTRY, VERSION
from capsid.sbom import pypi_lock_packages

PAGE_SIZE = 8
CLOSURE_PAGE_SIZE = 20

_CONDA_SUBDIR = {"linux-64": "linux/amd64", "linux-aarch64": "linux/arm64", "noarch": "any"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Artefact:
    name: str
    version: str
    build: str
    source: str  # conda channel name or "pypi"
    platform: str  # linux/amd64, linux/arm64, any
    license: str
    size: int | None
    sha256: str
    url: str


@dataclass
class Image:
    """One published image: a tool version or a stack version."""

    kind: str  # "tool" | "stack"
    name: str
    version: str
    description: str
    build_type: str
    platforms: list[str]
    test_command: str
    expected_exit_code: int
    definition_path: str  # path in the hub repository
    definition_text: str
    lockfile_path: str | None
    closure: list[Artefact]
    digest: str | None = None
    recipe: Recipe | None = None
    stack: StackRecipe | None = None
    components: list[tuple[str, str]] = field(default_factory=list)

    @property
    def tag_ref(self) -> str:
        return f"{REGISTRY}/{self.name}:{self.version}"

    @property
    def pinned_ref(self) -> str:
        return f"{self.tag_ref}@{self.digest}" if self.digest else self.tag_ref

    @property
    def url(self) -> str:
        section = "tools" if self.kind == "tool" else "stacks"
        return f"{section}/{self.name}/{self.version}/index.html"

    @property
    def closure_platforms(self) -> list[str]:
        order = {"linux/amd64": 0, "linux/arm64": 1, "any": 2}
        return sorted({a.platform for a in self.closure}, key=lambda p: order.get(p, 9))

    def closure_for(self, platform: str) -> list[Artefact]:
        """Artefacts installed on one platform (platform-independent ones included)."""
        return [a for a in self.closure if a.platform in (platform, "any")]

    @property
    def closure_count(self) -> int:
        """Distinct artefacts on the first architecture (what one image actually contains)."""
        plats = [p for p in self.closure_platforms if p != "any"]
        return len(self.closure_for(plats[0])) if plats else len(self.closure)

    @property
    def closure_bytes(self) -> int | None:
        plats = [p for p in self.closure_platforms if p != "any"]
        arts = self.closure_for(plats[0]) if plats else self.closure
        sizes = [a.size for a in arts if a.size is not None]
        return sum(sizes) if sizes else None

    def licenses(self) -> list[tuple[str, int]]:
        plats = [p for p in self.closure_platforms if p != "any"]
        arts = self.closure_for(plats[0]) if plats else self.closure
        return Counter(a.license for a in arts).most_common()


@dataclass
class Tool:
    name: str
    description: str
    homepage: str
    license: str
    categories: list[str]
    doi: str | None
    edam_topics: list[str]
    edam_operations: list[str]
    versions: list[Image]  # newest first

    @property
    def latest(self) -> Image:
        return self.versions[0]

    @property
    def url(self) -> str:
        return f"tools/{self.name}/index.html"


# ---------------------------------------------------------------------------
# Reading the hub
# ---------------------------------------------------------------------------


def _version_key(v: str) -> tuple:
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-_+]", v))


def conda_closure(pixi_lock: str) -> list[Artefact]:
    data = yaml.safe_load(pixi_lock) or {}
    seen: dict[str, Artefact] = {}
    for entry in data.get("packages") or []:
        url = entry.get("conda") if isinstance(entry, dict) else None
        if not url or url in seen:
            continue
        parts = urlparse(url).path.rstrip("/").split("/")
        filename, subdir = parts[-1], parts[-2] if len(parts) >= 2 else ""
        channel = parts[-3] if len(parts) >= 3 else ""
        stem = re.sub(r"(\.conda|\.tar\.bz2)$", "", filename)
        try:
            name, version, build = stem.rsplit("-", 2)
        except ValueError:
            continue
        seen[url] = Artefact(
            name=name,
            version=version,
            build=build,
            source=channel,
            platform=_CONDA_SUBDIR.get(subdir, subdir),
            license=str(entry.get("license") or "NOASSERTION"),
            size=entry.get("size"),
            sha256=str(entry.get("sha256") or ""),
            url=url,
        )
    return sorted(seen.values(), key=lambda a: (a.platform, a.name.lower()))


def pypi_closure(requirements_lock: str) -> list[Artefact]:
    out = []
    for pkg in pypi_lock_packages(requirements_lock):
        checks = pkg.get("checksums") or [{}]
        out.append(
            Artefact(
                name=pkg["name"],
                version=pkg["versionInfo"],
                build="",
                source="pypi",
                platform="any",
                license="see PyPI",
                size=None,
                sha256=checks[0].get("checksumValue", ""),
                url=pkg["downloadLocation"],
            )
        )
    return sorted(out, key=lambda a: a.name)


def _closure(model: Recipe | StackRecipe) -> list[Artefact]:
    arts: list[Artefact] = []
    if model.pixi_lock:
        arts += conda_closure(model.pixi_lock)
    if model.requirements_lock:
        arts += pypi_closure(model.requirements_lock)
    return arts


def _lockfile_rel(definition: Path, hub: Path) -> str | None:
    for p in lock_paths(definition):
        if p.is_file():
            return p.relative_to(hub).as_posix()
    return None


def load_hub(hub: Path) -> tuple[list[Tool], list[Image]]:
    """Read every recipe and stack of a hub checkout."""
    by_tool: dict[str, list[Image]] = {}
    for path in sorted((hub / "recipes").rglob("recipe.yaml")):
        text = path.read_text()
        recipe = Recipe.model_validate(yaml.safe_load(text))
        attach_lockfiles(recipe, path)
        img = Image(
            kind="tool",
            name=recipe.name,
            version=recipe.version,
            description=recipe.description or "",
            build_type=recipe.build.type.value,
            platforms=[p.value for p in recipe.platforms],
            test_command=recipe.test.command,
            expected_exit_code=recipe.test.expected_exit_code,
            definition_path=path.relative_to(hub).as_posix(),
            definition_text=text,
            lockfile_path=_lockfile_rel(path, hub),
            closure=_closure(recipe),
            recipe=recipe,
        )
        by_tool.setdefault(recipe.name, []).append(img)

    tools = []
    for name, images in sorted(by_tool.items()):
        images.sort(key=lambda i: _version_key(i.version), reverse=True)
        r = images[0].recipe
        assert r is not None
        tools.append(
            Tool(
                name=name,
                description=r.description or "",
                homepage=r.homepage or "",
                license=r.license or "NOASSERTION",
                categories=[c for c in r.categories if c != "bioconda-migrated"],
                doi=r.doi,
                edam_topics=r.edam_topics,
                edam_operations=r.edam_operations,
                versions=images,
            )
        )

    latest = {t.name: t.latest.version for t in tools}
    stacks: list[Image] = []
    stacks_dir = hub / "stacks"
    for path in sorted(stacks_dir.glob("*.yaml")) if stacks_dir.is_dir() else []:
        text = path.read_text()
        stack = StackRecipe.model_validate(yaml.safe_load(text))
        attach_lockfiles(stack, path)
        components = [(c.recipe, c.version or latest.get(c.recipe, "?")) for c in stack.components]
        comp_imgs = [i for t in tools for i in t.versions if (i.name, i.version) in set(components)]
        platforms = [p for p in ("linux/amd64", "linux/arm64") if all(p in i.platforms for i in comp_imgs)]
        build_types = sorted(
            {i.build_type for t in tools for i in t.versions if (i.name, i.version) in set(components)}
        )
        stacks.append(
            Image(
                kind="stack",
                name=stack.name,
                version=stack.version,
                description=stack.description or "",
                build_type=" + ".join(build_types) or "stack",
                platforms=platforms,
                test_command=stack.test.command,
                expected_exit_code=stack.test.expected_exit_code,
                definition_path=path.relative_to(hub).as_posix(),
                definition_text=text,
                lockfile_path=_lockfile_rel(path, hub),
                closure=_closure(stack),
                stack=stack,
                components=components,
            )
        )
    return tools, stacks


# ---------------------------------------------------------------------------
# Registry digests
# ---------------------------------------------------------------------------

_MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def resolve_digests(images: list[Image], timeout: float = 15.0) -> int:
    """Fill Image.digest from the registry (anonymous pull token). Returns how many resolved."""
    import httpx

    host, _, repo_prefix = REGISTRY.partition("/")
    resolved = 0
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for img in images:
            repo = f"{repo_prefix}/{img.name}"
            try:
                tok = client.get(f"https://{host}/token", params={"scope": f"repository:{repo}:pull"})
                token = tok.json().get("token", "") if tok.status_code == 200 else ""
                headers = {"Accept": _MANIFEST_TYPES}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                r = client.head(f"https://{host}/v2/{repo}/manifests/{img.version}", headers=headers)
                digest = r.headers.get("docker-content-digest", "")
                if r.status_code == 200 and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    img.digest = digest
                    resolved += 1
            except httpx.HTTPError:
                continue
    return resolved


# ---------------------------------------------------------------------------
# Figure 1: the icosahedral capsid (static frame; site.js animates it)
# ---------------------------------------------------------------------------


def icosahedron() -> tuple[list[tuple[float, float, float]], list[tuple[int, int]]]:
    phi = (1 + 5**0.5) / 2
    verts = []
    for a in (-1, 1):
        for b in (-phi, phi):
            verts += [(0.0, a, b), (a, b, 0.0), (b, 0.0, a)]
    edges = [
        (i, j)
        for i in range(len(verts))
        for j in range(i + 1, len(verts))
        if abs(math.dist(verts[i], verts[j]) - 2) < 1e-6
    ]
    return verts, edges


def capsid_svg_lines(ax: float = 0.42, ay: float = 0.63, scale: float = 88.0, c: float = 120.0) -> list[dict[str, Any]]:
    verts, edges = icosahedron()

    def rot(p: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = p
        x, z = x * math.cos(ay) + z * math.sin(ay), -x * math.sin(ay) + z * math.cos(ay)
        y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
        return x, y, z

    r = [rot(v) for v in verts]
    n = math.hypot(1, (1 + 5**0.5) / 2)
    lines = []
    for i, j in edges:
        depth = (r[i][2] + r[j][2]) / 2 / n
        lines.append(
            {
                "x1": round(c + r[i][0] / n * scale, 2),
                "y1": round(c + r[i][1] / n * scale, 2),
                "x2": round(c + r[j][0] / n * scale, 2),
                "y2": round(c + r[j][1] / n * scale, 2),
                "back": depth < 0,
            }
        )
    return lines


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def human_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    size = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{n} B"


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("capsid", "templates/site"),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["bytes"] = human_bytes
    env.filters["short"] = lambda d: (d or "").removeprefix("sha256:")[:12]
    env.filters["arch"] = lambda p: p.split("/")[-1]
    env.globals.update(
        REGISTRY=REGISTRY,
        HUB_URL=HUB_URL,
        HUB_REPO=HUB_REPO,
        ENGINE_URL=ENGINE_URL,
        ENGINE_VERSION=VERSION,
        CLOSURE_PAGE_SIZE=CLOSURE_PAGE_SIZE,
    )
    return env


def _write(out: Path, rel: str, html: str) -> None:
    target = out / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html)


def _root(rel: str) -> str:
    return "../" * rel.count("/")


def _script_json(value: Any) -> str:
    """JSON safe to embed in a <script type="application/json"> element."""
    text = json.dumps(value, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _closure_json(img: Image) -> str:
    rows = [[a.name, a.version, a.build, a.source, a.platform, a.license, a.size, a.sha256, a.url] for a in img.closure]
    return _script_json(rows)


def _index_row(t: Tool) -> dict[str, Any]:
    return {
        "name": t.name,
        "description": t.description,
        "versions": [v.version for v in t.versions],
        "build": t.latest.build_type,
        "platforms": [p.split("/")[-1] for p in t.latest.platforms],
        "closure": t.latest.closure_count,
        "license": t.license,
        "categories": t.categories,
        "url": t.url,
    }


def build_site(
    hub: Path,
    out: Path,
    page_size: int = PAGE_SIZE,
    digests: bool = False,
    build_date: str | None = None,
) -> dict[str, int]:
    """Render the hub website into `out` (replaced). Returns page counts."""
    tools, stacks = load_hub(hub)
    images = [i for t in tools for i in t.versions] + stacks
    resolved = resolve_digests(images) if digests else 0

    if build_date is None:
        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        moment = datetime.fromtimestamp(int(epoch), UTC) if epoch else datetime.now(UTC)
        build_date = moment.strftime("%Y-%m-%d")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    env = _env()

    pages_n = max(1, math.ceil(len(tools) / page_size))
    inventory = {
        "tools": len(tools),
        "images": len(images),
        "tool_images": len(images) - len(stacks),
        "stacks": len(stacks),
        "artefacts": len({a.url for i in images for a in i.closure}),
        "bytes": sum({a.url: a.size or 0 for i in images for a in i.closure}.values()),
        "multiarch": sum(1 for i in images if len(i.platforms) > 1),
        "digests": resolved,
        "conda": sum(1 for i in images if "pixi" in i.build_type),
        "pypi": sum(1 for i in images if "uv" in i.build_type),
    }
    common = {"build_date": build_date, "inventory": inventory, "digests_resolved": digests}

    def render(template: str, rel: str, **ctx: Any) -> None:
        html = env.get_template(template).render(root=_root(rel), page=rel, **common, **ctx)
        _write(out, rel, html)

    # Presentation page
    example = next((i for t in tools for i in t.versions if t.name == "samtools"), images[0] if images else None)
    render("index.html.j2", "index.html", tools=tools, stacks=stacks, example=example, capsid_lines=capsid_svg_lines())

    # Catalogue, statically paginated; site.js adds search and filters over the full index
    index = [_index_row(t) for t in tools]
    categories = sorted({c for t in tools for c in t.categories})
    builds = sorted({t.latest.build_type for t in tools})
    multi = sum(1 for t in tools if len(t.latest.platforms) > 1)
    composition = [
        ("Build", Counter(t.latest.build_type for t in tools).most_common()),
        ("Architectures", [(k, n) for k, n in (("amd64 + arm64", multi), ("amd64 only", len(tools) - multi)) if n]),
        ("Licence", Counter(t.license for t in tools).most_common()),
    ]
    for n in range(1, pages_n + 1):
        rel = "catalogue/index.html" if n == 1 else f"catalogue/page/{n}/index.html"
        render(
            "catalogue.html.j2",
            rel,
            tools=tools[(n - 1) * page_size : n * page_size],
            page_no=n,
            pages_n=pages_n,
            page_size=page_size,
            total=len(tools),
            index_json=_script_json(index),
            categories=categories,
            builds=builds,
            composition=composition,
            stacks_n=len(stacks),
        )

    for t in tools:
        render("tool.html.j2", t.url, tool=t)
        for img in t.versions:
            render("image.html.j2", img.url, img=img, tool=t, closure_json=_closure_json(img))

    render("stacks.html.j2", "stacks/index.html", stacks=stacks)
    tool_by_name = {t.name: t for t in tools}
    for s in stacks:
        render("image.html.j2", s.url, img=s, tool=None, tools=tool_by_name, closure_json=_closure_json(s))

    render("about.html.j2", "method/index.html")
    render("using.html.j2", "guide/using/index.html")
    render("contributing.html.j2", "guide/contributing/index.html")

    # Static assets and machine-readable index
    static = resources.files("capsid") / "templates" / "site" / "static"
    (out / "assets").mkdir()
    for name in ("site.css", "site.js", "favicon.svg"):
        (out / "assets" / name).write_text((static / name).read_text())
    recipes = [i.recipe for i in images if i.recipe is not None]
    catalog = build_catalog_dict(recipes)
    for t in tools:
        for img in t.versions:
            if img.digest:
                catalog["tools"][t.name]["versions"][img.version]["digest"] = img.digest
    (out / "catalog.json").write_text(json.dumps(catalog, indent=2))
    (out / ".nojekyll").write_text("")

    return {"tools": len(tools), "images": len(images), "catalogue_pages": pages_n, "digests": resolved}
