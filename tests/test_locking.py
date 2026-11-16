"""
Tests for lockfile support and apt snapshot pinning.
"""

import pytest
from pydantic import ValidationError

from capsid.builders.dockerfile_gen import DEFAULT_APT_SNAPSHOT, generate_dockerfile
from capsid.linter import lint_recipe
from capsid.locking import attach_lockfiles, conda_spec_to_pixi, lock_paths, pixi_manifest
from capsid.models import BuildConfig, BuildType, Platform, Recipe, StackRecipe, TestConfig


def _pixi(**kw):
    return Recipe(
        name="bwa", version="0.7.19",
        build=BuildConfig(type=BuildType.PIXI_CONDA, conda_packages=["bwa==0.7.19"], **kw),
        test=TestConfig(command="bwa"),
    )


def _uv(**kw):
    return Recipe(
        name="cutadapt", version="5.2",
        build=BuildConfig(type=BuildType.UV_PYPI, package="cutadapt==5.2", **kw),
        test=TestConfig(command="cutadapt --version"),
    )


@pytest.mark.parametrize("spec,expected", [
    ("samtools==1.25", ("samtools", '"==1.25"')),
    ("bwa=0.7.19", ("bwa", '"0.7.19.*"')),
    ("zlib", ("zlib", '"*"')),
    ("htslib >=1.20,<2", ("htslib", '">=1.20,<2"')),
    ("bioconda::fastp==1.4.0", ("fastp", '{ version = "==1.4.0", channel = "bioconda" }')),
])
def test_conda_spec_to_pixi(spec, expected):
    assert conda_spec_to_pixi(spec) == expected


def test_pixi_manifest_is_deterministic():
    args = ("bwa", ["conda-forge", "bioconda"], ["bwa==0.7.19"], [Platform.LINUX_AMD64, Platform.LINUX_ARM64])
    m = pixi_manifest(*args)
    assert m == pixi_manifest(*args)
    assert 'platforms = ["linux-64", "linux-aarch64"]' in m
    assert '"bwa" = "==0.7.19"' in m


def test_lock_paths():
    assert lock_paths(__import__("pathlib").Path("r/bwa/1/recipe.yaml"))[0].as_posix() == "r/bwa/1/pixi.lock"
    assert lock_paths(__import__("pathlib").Path("stacks/qc.yaml"))[1].as_posix() == "stacks/qc.requirements.lock"


def test_attach_lockfiles_and_not_serialized(tmp_path):
    (tmp_path / "recipe.yaml").write_text("x")
    (tmp_path / "pixi.lock").write_text("version: 6\n")
    r = attach_lockfiles(_pixi(), tmp_path / "recipe.yaml")
    assert r.pixi_lock == "version: 6\n"
    assert "pixi_lock" not in r.model_dump()


def test_locked_pixi_dockerfile():
    r = _pixi()
    r.pixi_lock = "version: 6\nenvironments: {}\n"
    df = generate_dockerfile(r)
    assert "RUN pixi install --locked" in df
    assert "COPY <<'EOF_PIXI_LOCK' /build/pixi.lock\nversion: 6" in df
    assert '"bwa" = "==0.7.19"' in df
    assert "pixi add" not in df
    # lock content must not leak into the embedded recipe.yaml
    assert "pixi_lock" not in df


def test_unlocked_pixi_falls_back_to_solver():
    df = generate_dockerfile(_pixi())
    assert 'pixi add "bwa==0.7.19"' in df
    assert "--locked" not in df


def test_locked_uv_dockerfile():
    r = _uv()
    r.requirements_lock = "cutadapt==5.2 \\\n    --hash=sha256:" + "a" * 64 + "\n"
    df = generate_dockerfile(r)
    assert "uv pip install --no-cache --require-hashes -r /build/requirements.lock" in df
    assert 'uv pip install --no-cache "cutadapt==5.2"' not in df


def test_apt_snapshot_default_and_overrides():
    df = generate_dockerfile(_pixi())
    assert f"snapshot.debian.org/archive/debian/{DEFAULT_APT_SNAPSHOT}" in df
    assert "Check-Valid-Until: no" in df
    assert "/var/log/dpkg.log" in df  # timestamped logs removed for bit-for-bit layers

    assert "snapshot.debian.org/archive/debian/20250101T000000Z" in generate_dockerfile(
        _pixi(apt_snapshot="20250101T000000Z"))
    assert "snapshot.debian.org" not in generate_dockerfile(_pixi(apt_snapshot="latest"))
    # custom base images may not be Debian bookworm: leave their apt sources alone
    assert "snapshot.debian.org" not in generate_dockerfile(_pixi(base_image="ubuntu:24.04@sha256:" + "b" * 64))


def test_apt_snapshot_validation():
    with pytest.raises(ValidationError):
        _pixi(apt_snapshot="2025-01-01")
    with pytest.raises(ValidationError):
        StackRecipe(name="s", version="1", components=[], test=TestConfig(command="x"), apt_snapshot="nope")


def test_linter_warns_missing_lockfile(tmp_path):
    d = tmp_path / "bwa" / "0.7.19"
    d.mkdir(parents=True)
    (d / "recipe.yaml").write_text(
        "name: bwa\nversion: '0.7.19'\nlicense: GPL-3.0\ndescription: Burrows-Wheeler aligner\n"
        "build:\n  type: pixi-conda\n  conda_packages: ['bwa==0.7.19']\ntest:\n  command: bwa 2>&1 | grep Version\n"
    )
    rules = [i.rule for i in lint_recipe(d / "recipe.yaml")]
    assert "W014_MISSING_LOCKFILE" in rules
    (d / "pixi.lock").write_text("version: 6\n")
    rules = [i.rule for i in lint_recipe(d / "recipe.yaml")]
    assert "W014_MISSING_LOCKFILE" not in rules
