"""Embedded SPDX SBOM: locked closure, checksums, purls, determinism."""

from capsid.models import Recipe
from capsid.sbom import conda_lock_packages, generate_spdx_sbom, pypi_lock_packages

PIXI_LOCK = """version: 7
packages:
- conda: https://conda.anaconda.org/bioconda/linux-64/samtools-1.25-h9dcdb79_0.conda
  sha256: 92282894edc9e5c1ae4d0ea21a6e4e58257716ee0b4ad81d6fc8b0dc89d35692
  license: MIT
- conda: https://conda.anaconda.org/conda-forge/noarch/tzdata-2025b-h78e105d_0.conda
  sha256: 5aaa366385d716557e365f0a4e9c3fca43ba196872abbbe3d56bb610d131e192
  license: LicenseRef-Public-Domain
- conda: https://conda.anaconda.org/conda-forge/linux-64/libgcc-15.1.0-h767d61c_2.conda
  sha256: 0024f9ab34c09629621aefd8603ef77bf9d708129b0dd79029e502c39ffc2195
  license: GPL-3.0-only WITH GCC-exception-3.1
- conda: https://conda.anaconda.org/conda-forge/linux-64/weird-1.0-h0_0.conda
  license: BSD
"""

REQ_LOCK = """# generated
cutadapt==5.2 \\
    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \\
    --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
dnaio==1.2.3 ; python_full_version < '3.14' \\
    --hash=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
"""


def test_conda_lock_packages():
    pkgs = {p["name"]: p for p in conda_lock_packages(PIXI_LOCK)}
    sam = pkgs["samtools"]
    assert sam["versionInfo"] == "1.25"
    assert sam["checksums"][0]["checksumValue"].startswith("92282894")
    assert sam["externalRefs"][0]["referenceLocator"].startswith("pkg:conda/samtools@1.25?build=h9dcdb79_0")
    assert "subdir=noarch" in pkgs["tzdata"]["externalRefs"][0]["referenceLocator"]
    assert pkgs["libgcc"]["licenseDeclared"] == "GPL-3.0-only WITH GCC-exception-3.1"
    assert "checksums" not in pkgs["weird"]


def test_pypi_lock_packages():
    pkgs = {p["name"]: p for p in pypi_lock_packages(REQ_LOCK)}
    assert set(pkgs) == {"cutadapt", "dnaio"}
    assert pkgs["cutadapt"]["checksums"][0]["checksumValue"] == "a" * 64
    assert pkgs["dnaio"]["versionInfo"] == "1.2.3"
    assert pkgs["dnaio"]["externalRefs"][0]["referenceLocator"] == "pkg:pypi/dnaio@1.2.3"


def _recipe(**build):
    return Recipe.model_validate(
        {"name": "tool", "version": "1.0", "license": "MIT", "build": build, "test": {"command": "tool --help"}}
    )


def test_recipe_sbom_uses_lockfile_and_is_deterministic():
    r = _recipe(type="pixi-conda", conda_packages=["samtools==1.25"])
    r.pixi_lock = PIXI_LOCK
    doc = generate_spdx_sbom(r)
    assert doc == generate_spdx_sbom(r)
    assert doc["creationInfo"]["created"] == "2023-11-14T22:13:20Z"  # SOURCE_DATE_EPOCH
    ids = [p["SPDXID"] for p in doc["packages"]]
    assert len(ids) == len(set(ids)) == 5
    contains = [r for r in doc["relationships"] if r["relationshipType"] == "CONTAINS"]
    assert len(contains) == 4


def test_source_and_binary_inputs_carry_checksums():
    src = generate_spdx_sbom(_recipe(type="make", source="https://e.org/t.tgz", sha256="d" * 64))
    assert src["packages"][1]["downloadLocation"] == "https://e.org/t.tgz"
    assert src["packages"][1]["checksums"][0]["checksumValue"] == "d" * 64
    binary = generate_spdx_sbom(
        _recipe(
            type="binary",
            binary_url_amd64="https://e.org/a",
            sha256_amd64="e" * 64,
            binary_url_arm64="https://e.org/b",
            sha256_arm64="f" * 64,
        )
    )
    assert [p["name"] for p in binary["packages"][1:]] == ["tool-linux-amd64", "tool-linux-arm64"]


def test_non_spdx_license_becomes_noassertion():
    r = _recipe(type="make", source="https://e.org/t.tgz", sha256="d" * 64)
    r.license = "GNU General Public License v3"
    assert generate_spdx_sbom(r)["packages"][0]["licenseDeclared"] == "NOASSERTION"
