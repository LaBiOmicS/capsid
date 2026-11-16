"""Recipe and stack linter: every rule is exercised through its stable code."""

import copy
import json
from pathlib import Path

import pytest
import yaml

from capsid.linter import lint_path, lint_recipe, lint_stack

GOOD = {
    "name": "fastp",
    "version": "1.4.0",
    "description": "Fast all-in-one FASTQ preprocessor",
    "homepage": "https://github.com/OpenGene/fastp",
    "license": "MIT",
    "platforms": ["linux/amd64", "linux/arm64"],
    "build": {"type": "pixi-conda", "conda_packages": ["fastp==1.4.0"]},
    "test": {"command": "fastp --help"},
}
PIXI_LOCK = """version: 7
platforms:
- name: linux-64
- name: linux-aarch64
packages:
- conda: https://conda.anaconda.org/bioconda/linux-64/fastp-1.4.0-h1_0.conda
- conda: https://conda.anaconda.org/bioconda/linux-aarch64/fastp-1.4.0-h2_0.conda
"""
SRC = {"source": "https://example.org/x.tar.gz", "sha256": "a" * 64}


def write_recipe(root: Path, data: dict, lock: str = PIXI_LOCK, lock_name: str = "pixi.lock") -> Path:
    d = root / "recipes" / str(data.get("name")) / str(data.get("version"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "recipe.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    if lock:
        (d / lock_name).write_text(lock)
    return d / "recipe.yaml"


def codes(issues, level=None):
    return {i.rule for i in issues if level is None or i.level == level}


def variant(**changes):
    data = copy.deepcopy(GOOD)
    for dotted, value in changes.items():
        target = data
        *path, last = dotted.split("__")
        for key in path:
            target = target.setdefault(key, {})
        if value is None:
            target.pop(last, None)
        else:
            target[last] = value
    return data


def test_good_recipe_is_clean(tmp_path):
    assert lint_recipe(write_recipe(tmp_path, GOOD)) == []


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"name": "FastP"}, "E005_INVALID_NAME"),
        ({"test__command": "true"}, "E030_DUMMY_TEST"),
        ({"test__command": ""}, "E030_DUMMY_TEST"),
        ({"platforms": []}, "E007_NO_PLATFORMS"),
        ({"build__conda_packages": []}, "E012_MISSING_BUILD_INPUT"),
        ({"build": {"type": "uv-pypi"}}, "E012_MISSING_BUILD_INPUT"),
        ({"build": {"type": "cmake"}}, "E012_MISSING_BUILD_INPUT"),
        ({"build": {"type": "cmake", "source": "https://e.org/x.tgz"}}, "E011_MISSING_SHA256"),
        (
            {"build": {"type": "binary", "binary_url_amd64": "https://e.org/a", "sha256_amd64": "a" * 64}},
            "E012_MISSING_BUILD_INPUT",
        ),
        (
            {"build": {"type": "binary", "binary_url_amd64": "https://e.org/a", "binary_url_arm64": "https://e.org/b"}},
            "E011_MISSING_SHA256",
        ),
        ({"build": {"type": "uv-pypi", "package": "x==1", "python_version": "2.7"}}, "E013_UNSUPPORTED_PYTHON"),
        (
            {"build": {"type": "make", **SRC, "build_commands": ["curl -fsSL https://x.sh | bash"]}},
            "E010_DANGEROUS_COMMAND",
        ),
        ({"build": {"type": "make", **SRC, "build_commands": ["sudo make install"]}}, "E010_DANGEROUS_COMMAND"),
        ({"build": {"type": "make", **SRC, "build_commands": ["chmod -R 777 /opt"]}}, "E010_DANGEROUS_COMMAND"),
        (
            {"build": {"type": "make", **SRC, "build_commands": ["wget --no-check-certificate https://x"]}},
            "E010_DANGEROUS_COMMAND",
        ),
        ({"test__expected_checksum": "b" * 64}, "E015_CHECKSUM_WITHOUT_OUTPUT"),
        ({"build__conda_packages": ["fastp==1.5.0"]}, "E016_STALE_LOCKFILE"),
    ],
)
def test_errors(tmp_path, changes, code):
    assert code in codes(lint_recipe(write_recipe(tmp_path, variant(**changes))), "ERROR")


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"license": None}, "W020_MISSING_LICENSE"),
        ({"license": "GNU GENERAL PUBLIC LICENSE Version 3"}, "W022_INVALID_LICENSE"),
        ({"description": "x"}, "W021_SHORT_DESCRIPTION"),
        ({"homepage": None}, "W023_MISSING_HOMEPAGE"),
        ({"build__conda_packages": ["fastp"]}, "W012_UNPINNED_PACKAGE"),
        ({"build__conda_packages": ["fastp>=1.0"]}, "W012_UNPINNED_PACKAGE"),
        ({"build__base_image": "debian:bookworm-slim"}, "W017_UNPINNED_BASE_IMAGE"),
        ({"build__dependencies": {"build": [], "runtime": ["zlib1g"]}}, "W018_UNKNOWN_DEPENDENCY_GROUP"),
        ({"build__cmake_args": ["-DX=1"]}, "W019_IGNORED_FIELD"),
        ({"build__package": "fastp==1.4.0"}, "W019_IGNORED_FIELD"),
        (
            {"build__base_image": "x@sha256:" + "a" * 64, "build__apt_snapshot": "20260101T000000Z"},
            "W019_IGNORED_FIELD",
        ),
        ({"build": {"type": "make", "source": "http://e.org/x.tgz", "sha256": "a" * 64}}, "W013_INSECURE_HTTP"),
        (
            {"build": {"type": "make", **SRC, "build_commands": ["curl -fsSLO https://e.org/patch.diff"]}},
            "W010_DOWNLOAD_IN_BUILD_COMMAND",
        ),
        ({"platforms": ["linux/amd64", "linux/amd64"]}, "W011_DUPLICATE_PLATFORM"),
    ],
)
def test_warnings(tmp_path, changes, code):
    issues = lint_recipe(write_recipe(tmp_path, variant(**changes)))
    assert code in codes(issues, "WARNING"), [str(i) for i in issues]


def test_layout_mismatch(tmp_path):
    p = write_recipe(tmp_path, GOOD)
    moved = tmp_path / "recipes" / "other" / "9.9"
    moved.mkdir(parents=True)
    (moved / "recipe.yaml").write_text(p.read_text())
    assert {"E003_NAME_PATH_MISMATCH", "E004_VERSION_PATH_MISMATCH"} <= codes(lint_recipe(moved / "recipe.yaml"))


def test_missing_and_invalid_files(tmp_path):
    assert codes(lint_recipe(tmp_path / "nope.yaml")) == {"E001_FILE_NOT_FOUND"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: [unclosed")
    assert codes(lint_recipe(bad)) == {"E002_SCHEMA_INVALID"}
    bad.write_text("name: x\nversion: 1\n")
    assert codes(lint_recipe(bad)) == {"E002_SCHEMA_INVALID"}


def test_lockfile_rules(tmp_path):
    assert "W014_MISSING_LOCKFILE" in codes(lint_recipe(write_recipe(tmp_path / "a", GOOD, lock="")))
    amd_only = PIXI_LOCK.replace("- name: linux-aarch64\n", "")
    assert "E017_LOCKFILE_PLATFORM" in codes(lint_recipe(write_recipe(tmp_path / "b", GOOD, lock=amd_only)))
    uv = variant(build={"type": "uv-pypi", "package": "cutadapt==5.2"})
    ok = lint_recipe(
        write_recipe(tmp_path / "c", uv, lock="cutadapt==5.2 \\\n    --hash=sha256:x\n", lock_name="requirements.lock")
    )
    assert "E016_STALE_LOCKFILE" not in codes(ok)
    stale = lint_recipe(write_recipe(tmp_path / "d", uv, lock="cutadapt==5.1 \\\n", lock_name="requirements.lock"))
    assert "E016_STALE_LOCKFILE" in codes(stale)


def test_published_version_only_with_catalog(tmp_path):
    p = write_recipe(tmp_path, GOOD)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"tools": {"fastp": {"versions": {"1.4.0": {}}}}}))
    assert "W040_PUBLISHED_VERSION" in codes(lint_recipe(p, catalog_path=catalog))
    assert "W040_PUBLISHED_VERSION" not in codes(lint_recipe(p))


def _stack(tmp_path, data, lock=None):
    d = tmp_path / "stacks"
    d.mkdir(exist_ok=True)
    p = d / f"{data['name']}.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    if lock is not None:
        (d / f"{data['name']}.pixi.lock").write_text(lock)
    return p


STACK = {
    "name": "qc",
    "version": "1.0.0",
    "components": [{"recipe": "fastp", "version": "1.4.0"}],
    "test": {"command": "fastp --help"},
}


def test_stack_rules(tmp_path, monkeypatch):
    write_recipe(tmp_path, GOOD)
    monkeypatch.setenv("CAPSID_RECIPES_PATH", str(tmp_path / "recipes"))
    monkeypatch.chdir(tmp_path)

    good = _stack(tmp_path, STACK, lock=PIXI_LOCK)
    assert lint_stack(good) == []
    assert lint_path(good) == []

    assert "W014_MISSING_LOCKFILE" in codes(lint_stack(_stack(tmp_path, {**STACK, "name": "nolock"})))
    missing = {**STACK, "name": "missing", "components": [{"recipe": "nosuchtool", "version": "1"}]}
    assert "E051_COMPONENT_NOT_FOUND" in codes(lint_stack(_stack(tmp_path, missing)))
    dup = {**STACK, "name": "dup", "components": STACK["components"] * 2}
    assert "E054_DUPLICATE_COMPONENT" in codes(lint_stack(_stack(tmp_path, dup, lock=PIXI_LOCK)))
    unpinned = {**STACK, "name": "unpinned", "components": [{"recipe": "fastp"}]}
    assert "W051_UNPINNED_COMPONENT" in codes(lint_stack(_stack(tmp_path, unpinned, lock=PIXI_LOCK)))
    trivial = {**STACK, "name": "trivial", "test": {"command": "true"}}
    assert "E030_DUMMY_TEST" in codes(lint_stack(_stack(tmp_path, trivial, lock=PIXI_LOCK)))

    write_recipe(tmp_path, variant(name="mk", build={"type": "make", **SRC}))
    src = {**STACK, "name": "src", "components": [{"recipe": "mk", "version": "1.4.0"}]}
    assert "E052_UNSUPPORTED_COMPONENT" in codes(lint_stack(_stack(tmp_path, src)))

    write_recipe(tmp_path, variant(name="amdonly", platforms=["linux/amd64"], build__conda_packages=["amdonly==1.4.0"]))
    write_recipe(tmp_path, variant(name="armonly", platforms=["linux/arm64"], build__conda_packages=["armonly==1.4.0"]))
    split = {
        **STACK,
        "name": "split",
        "components": [{"recipe": "amdonly", "version": "1.4.0"}, {"recipe": "armonly", "version": "1.4.0"}],
    }
    assert "E053_NO_COMMON_PLATFORM" in codes(lint_stack(_stack(tmp_path, split)))

    renamed = _stack(tmp_path, {**STACK, "name": "other"})
    renamed.rename(tmp_path / "stacks" / "wrong.yaml")
    assert "E003_NAME_PATH_MISMATCH" in codes(lint_stack(tmp_path / "stacks" / "wrong.yaml"))
