"""
Tests for the hub website generator (capsid.site).
"""

import json
import re
from pathlib import Path

from capsid.site import build_site, conda_closure, icosahedron, load_hub

PIXI_LOCK = """\
version: 6
environments:
  default:
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/bioconda/linux-64/demo-1.0-h0_0.conda
      - conda: https://conda.anaconda.org/conda-forge/noarch/ca-certificates-2026.1-h0_0.conda
      linux-aarch64:
      - conda: https://conda.anaconda.org/bioconda/linux-aarch64/demo-1.0-h1_0.conda
      - conda: https://conda.anaconda.org/conda-forge/noarch/ca-certificates-2026.1-h0_0.conda
packages:
- conda: https://conda.anaconda.org/bioconda/linux-64/demo-1.0-h0_0.conda
  sha256: aaaa
  license: MIT
  size: 1000
- conda: https://conda.anaconda.org/bioconda/linux-aarch64/demo-1.0-h1_0.conda
  sha256: bbbb
  license: MIT
  size: 1100
- conda: https://conda.anaconda.org/conda-forge/noarch/ca-certificates-2026.1-h0_0.conda
  sha256: cccc
  license: MPL-2.0
  size: 200
"""


def _recipe(name: str, version: str, platforms: list[str], description: str = "Demo tool for tests") -> str:
    plats = "\n".join(f"- {p}" for p in platforms)
    return f"""\
name: {name}
version: '{version}'
description: {description}
homepage: https://example.org/{name}
license: MIT
categories: [genomics]
platforms:
{plats}
build:
  type: pixi-conda
  conda_packages: [{name}=={version}]
  channels: [conda-forge, bioconda]
test:
  command: {name} --version
"""


def _hub(tmp_path: Path, n_tools: int = 3) -> Path:
    hub = tmp_path / "hub"
    for i in range(n_tools):
        name = "demo" if i == 0 else f"tool{i}"
        d = hub / "recipes" / name / "1.0"
        d.mkdir(parents=True)
        (d / "recipe.yaml").write_text(_recipe(name, "1.0", ["linux/amd64", "linux/arm64"]))
        (d / "pixi.lock").write_text(PIXI_LOCK)
    old = hub / "recipes" / "demo" / "0.9"
    old.mkdir(parents=True)
    (old / "recipe.yaml").write_text(_recipe("demo", "0.9", ["linux/amd64"], "Old <script>alert(1)</script>"))
    (hub / "stacks").mkdir()
    (hub / "stacks" / "pair.yaml").write_text(
        "name: pair\nversion: 1.0.0\ndescription: Two tools\ncomponents:\n"
        "  - recipe: demo\n    version: '0.9'\n  - recipe: tool1\n    version: '1.0'\n"
        "test:\n  command: demo --version && tool1 --version\n"
    )
    return hub


def test_conda_closure_per_platform():
    arts = conda_closure(PIXI_LOCK)
    assert {(a.name, a.platform) for a in arts} == {
        ("demo", "linux/amd64"),
        ("demo", "linux/arm64"),
        ("ca-certificates", "any"),
    }
    demo = next(a for a in arts if a.platform == "linux/amd64")
    assert (demo.version, demo.build, demo.source, demo.sha256, demo.size) == ("1.0", "h0_0", "bioconda", "aaaa", 1000)


def test_load_hub_orders_versions_and_derives_stack_platforms(tmp_path):
    tools, stacks = load_hub(_hub(tmp_path))
    demo = next(t for t in tools if t.name == "demo")
    assert [v.version for v in demo.versions] == ["1.0", "0.9"]
    assert demo.latest.closure_count == 2  # demo + noarch ca-certificates on one architecture
    (pair,) = stacks
    assert pair.components == [("demo", "0.9"), ("tool1", "1.0")]
    assert pair.platforms == ["linux/amd64"]  # demo 0.9 is amd64-only


def test_build_site_pages_pagination_and_relative_links(tmp_path):
    out = tmp_path / "site"
    stats = build_site(_hub(tmp_path, n_tools=5), out, page_size=2, build_date="2026-01-01")
    assert stats == {"tools": 5, "images": 7, "catalogue_pages": 3, "digests": 0}
    for rel in [
        "index.html",
        "catalogue/index.html",
        "catalogue/page/2/index.html",
        "catalogue/page/3/index.html",
        "tools/demo/index.html",
        "tools/demo/1.0/index.html",
        "tools/demo/0.9/index.html",
        "stacks/index.html",
        "stacks/pair/1.0.0/index.html",
        "guide/using/index.html",
        "guide/contributing/index.html",
        "method/index.html",
        "assets/site.css",
        "assets/site.js",
        "catalog.json",
    ]:
        assert (out / rel).is_file(), rel
    assert not (out / "catalogue/page/4").exists()

    # Every relative link resolves to a generated file
    for page in out.rglob("*.html"):
        for href in re.findall(r'(?:href|src)="([^"#:]+)"', page.read_text()):
            assert (page.parent / href).resolve().is_file(), f"{page.relative_to(out)} -> {href}"

    page2 = (out / "catalogue/page/2/index.html").read_text()
    assert 'rel="prev"' in page2 and 'rel="next"' in page2
    assert "Showing 3\u20134 of 5; page 2 of 3." in page2
    assert 'href="../../../index.html">\u2190 Return to the overview' in page2


def test_untrusted_text_is_escaped(tmp_path):
    out = tmp_path / "site"
    build_site(_hub(tmp_path), out, build_date="2026-01-01")
    html = (out / "tools/demo/0.9/index.html").read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    for page in out.rglob("*.html"):
        for block in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', page.read_text(), re.S):
            json.loads(block)
            assert "</" not in block


def test_image_page_usage_without_digest_uses_tag(tmp_path):
    out = tmp_path / "site"
    build_site(_hub(tmp_path), out, build_date="2026-01-01")
    html = (out / "tools/demo/1.0/index.html").read_text()
    assert "ghcr.io/labiomics/capsid/demo:1.0" in html
    assert "@sha256:" not in html
    assert "capsid export nextflow demo@1.0" in html
    assert "Not resolved at compilation" in html


def test_icosahedron_geometry():
    verts, edges = icosahedron()
    assert (len(verts), len(edges)) == (12, 30)
