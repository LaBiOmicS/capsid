"""
Unit tests for Dockerfile generation.
"""

import pytest
from pydantic import ValidationError

from capsid.models import Recipe, BuildConfig, BuildType, TestConfig, StackRecipe, StackComponent
from capsid.builders.stack_builder import generate_stack_dockerfile
from capsid.builders.dockerfile_gen import generate_dockerfile


def test_dockerfile_pixi_hpc_hardening():
    recipe = Recipe(
        name="samtools",
        version="1.25",
        build=BuildConfig(
            type=BuildType.PIXI_CONDA,
            conda_packages=["samtools==1.25"]
        ),
        test=TestConfig(command="samtools --version")
    )
    df = generate_dockerfile(recipe)

    # Verify Pixi builder
    assert "ghcr.io/prefix-dev/pixi" in df
    assert 'pixi add "samtools==1.25"' in df

    # Verify HPC mount points
    assert "mkdir -p /scratch /data /work /lustre /shared /tmp /home/bio" in df
    assert "chmod 1777 /tmp /scratch /data /work /lustre /shared" in df

    # Verify subshell environment persistence
    assert "/etc/profile.d/capsid.sh" in df
    assert "/etc/environment" in df

    # Verify cache redirection
    assert 'XDG_CACHE_HOME="/tmp/.cache"' in df
    assert 'PYTHONPYCACHEPREFIX="/tmp/pycache"' in df

    # Verify non-root user and Tini
    assert "USER 1000:1000" in df
    assert "LD_LIBRARY_PATH=" not in df and "export LD_LIBRARY_PATH" not in df
    assert "@sha256:" in df.split("AS pixi_builder")[0]
    assert 'ENTRYPOINT ["/usr/bin/tini", "--"]' in df


def test_dockerfile_uv_pypi_hardening():
    recipe = Recipe(
        name="cutadapt",
        version="5.2",
        build=BuildConfig(
            type=BuildType.UV_PYPI,
            package="cutadapt==5.2"
        ),
        test=TestConfig(command="cutadapt --version")
    )
    df = generate_dockerfile(recipe)

    assert "ghcr.io/astral-sh/uv" in df
    assert 'uv pip install --no-cache "cutadapt==5.2"' in df
    assert "/scratch" in df
    assert 'io.capsid.orchestrators="nextflow,snakemake"' in df


def test_uv_runtime_matches_builder_python():
    """The venv points at the builder's interpreter, so runtime must be the same image."""
    recipe = Recipe(
        name="cutadapt", version="5.2",
        build=BuildConfig(type=BuildType.UV_PYPI, package="cutadapt==5.2", python_version="3.12"),
        test=TestConfig(command="cutadapt --version"),
    )
    df = generate_dockerfile(recipe)
    builder = df.split("AS python_builder")[0].splitlines()[-1].removeprefix("FROM ").strip()
    runtime = df.split("AS runtime")[0].splitlines()[-1].removeprefix("FROM ").strip()
    assert builder == runtime
    assert builder.startswith("python:3.12-slim-bookworm@sha256:")
    assert "python3-minimal" not in df
    assert "/etc/capsid/requirements.lock" in df


def test_unsupported_python_version_rejected():
    recipe = Recipe(
        name="x", version="1",
        build=BuildConfig(type=BuildType.UV_PYPI, package="x==1", python_version="2.7"),
        test=TestConfig(command="x --version"),
    )
    with pytest.raises(ValueError, match="Unsupported python_version"):
        generate_dockerfile(recipe)


def _source_recipe(**kw):
    return Recipe(
        name="demo", version="1.0",
        build=BuildConfig(type=BuildType.AUTOTOOLS, source="https://example.org/demo.tar.gz", **kw),
        test=TestConfig(command="demo --version"),
    )


def test_source_build_verifies_sha256_and_uses_rpath():
    df = generate_dockerfile(_source_recipe(sha256="A" * 64))
    assert f'echo "{"a" * 64}  archive.tar" | sha256sum -c -' in df
    assert "-Wl,-rpath,/opt/demo/lib" in df
    assert "LD_LIBRARY_PATH=" not in df and "export LD_LIBRARY_PATH" not in df


def test_source_build_without_sha256_refused():
    with pytest.raises(ValueError, match="sha256"):
        generate_dockerfile(_source_recipe())


def test_sha256_must_be_hex():
    with pytest.raises(ValidationError):
        _source_recipe(sha256="abc; rm -rf /")


def test_labels_are_escaped_and_continuations_valid():
    recipe = Recipe(
        name="samtools", version="1.25", description='Tool with "quotes" and \\ slash',
        doi="10.1093/x",
        build=BuildConfig(type=BuildType.PIXI_CONDA, conda_packages=["samtools==1.25"]),
        test=TestConfig(command="samtools --version"),
    )
    df = generate_dockerfile(recipe)
    assert 'description="Tool with \\"quotes\\" and \\\\ slash"' in df
    assert 'io.capsid.fair.module_nf="/etc/capsid/module.nf" \\\n' in df
    # No blank / whitespace-only line inside a line continuation
    lines = df.splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.endswith("\\"):
            assert lines[i + 1].strip(), f"empty continuation after line {i + 1}: {line!r}"


def test_stack_uses_shared_hardened_runtime():
    fastp = Recipe(name="fastp", version="1.4.0",
                   build=BuildConfig(type=BuildType.PIXI_CONDA, conda_packages=["fastp==1.4.0"]),
                   test=TestConfig(command="fastp --version"))
    multiqc = Recipe(name="multiqc", version="1.25.1",
                     build=BuildConfig(type=BuildType.UV_PYPI, package="multiqc==1.25.1"),
                     test=TestConfig(command="multiqc --version"))
    stack = StackRecipe(name="qc", version="1.0.0",
                        components=[StackComponent(recipe="fastp"), StackComponent(recipe="multiqc")],
                        test=TestConfig(command="fastp --version"))
    df = generate_stack_dockerfile(stack, [fastp, multiqc])
    assert "LD_LIBRARY_PATH=" not in df and "export LD_LIBRARY_PATH" not in df
    assert "USER 1000:1000" in df
    assert "/etc/capsid/pixi.lock" in df and "/etc/capsid/requirements.lock" in df
    for line in df.splitlines():
        if line.startswith("FROM "):
            assert "@sha256:" in line, line
