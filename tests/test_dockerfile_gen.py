"""
Unit tests for Dockerfile generation.
"""

import pytest
from pydantic import ValidationError

from capsid.builders.dockerfile_gen import generate_dockerfile
from capsid.builders.stack_builder import generate_stack_dockerfile
from capsid.models import BuildConfig, BuildType, Recipe, StackComponent, StackRecipe, TestConfig


def test_dockerfile_pixi_hpc_hardening():
    recipe = Recipe(
        name="samtools",
        version="1.25",
        build=BuildConfig(type=BuildType.PIXI_CONDA, conda_packages=["samtools==1.25"]),
        test=TestConfig(command="samtools --version"),
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
        build=BuildConfig(type=BuildType.UV_PYPI, package="cutadapt==5.2"),
        test=TestConfig(command="cutadapt --version"),
    )
    df = generate_dockerfile(recipe)

    assert "ghcr.io/astral-sh/uv" in df
    assert 'uv pip install --no-cache "cutadapt==5.2"' in df
    assert "/scratch" in df
    assert 'io.capsid.orchestrators="nextflow,snakemake"' in df


def test_uv_runtime_matches_builder_python():
    """The venv points at the builder's interpreter, so runtime must be the same image."""
    recipe = Recipe(
        name="cutadapt",
        version="5.2",
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
        name="x",
        version="1",
        build=BuildConfig(type=BuildType.UV_PYPI, package="x==1", python_version="2.7"),
        test=TestConfig(command="x --version"),
    )
    with pytest.raises(ValueError, match="Unsupported python_version"):
        generate_dockerfile(recipe)


def _source_recipe(**kw):
    return Recipe(
        name="demo",
        version="1.0",
        build=BuildConfig(type=BuildType.AUTOTOOLS, source="https://example.org/demo.tar.gz", **kw),
        test=TestConfig(command="demo --version"),
    )


def test_source_build_verifies_sha256_and_uses_rpath():
    df = generate_dockerfile(_source_recipe(sha256="A" * 64))
    assert f'echo "{"a" * 64}  archive" | sha256sum -c -' in df
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
        name="samtools",
        version="1.25",
        description='Tool with "quotes" and \\ slash',
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
    fastp = Recipe(
        name="fastp",
        version="1.4.0",
        build=BuildConfig(type=BuildType.PIXI_CONDA, conda_packages=["fastp==1.4.0"]),
        test=TestConfig(command="fastp --version"),
    )
    multiqc = Recipe(
        name="multiqc",
        version="1.25.1",
        build=BuildConfig(type=BuildType.UV_PYPI, package="multiqc==1.25.1"),
        test=TestConfig(command="multiqc --version"),
    )
    stack = StackRecipe(
        name="qc",
        version="1.0.0",
        components=[StackComponent(recipe="fastp"), StackComponent(recipe="multiqc")],
        test=TestConfig(command="fastp --version"),
    )
    df = generate_stack_dockerfile(stack, [fastp, multiqc])
    assert "LD_LIBRARY_PATH=" not in df and "export LD_LIBRARY_PATH" not in df
    assert "USER 1000:1000" in df
    assert "/etc/capsid/pixi.lock" in df and "/etc/capsid/requirements.lock" in df
    for line in df.splitlines():
        if line.startswith("FROM "):
            assert "@sha256:" in line, line


def _typed_recipe(build_type, **kw):
    return Recipe(
        name="demo",
        version="1.0",
        build=BuildConfig(type=build_type, **kw),
        test=TestConfig(command="demo --version"),
    )


SRC = {"source": "https://example.org/demo.tar.gz", "sha256": "b" * 64}


def test_cmake_build_uses_cmake_args_and_installs_cmake():
    df = generate_dockerfile(_typed_recipe(BuildType.CMAKE, cmake_args=["-DWITH_X=ON"], **SRC))
    assert "        cmake \\\n" in df
    assert "-DCMAKE_INSTALL_PREFIX=/opt/demo" in df and "-DWITH_X=ON" in df
    assert "cmake --install /src/build" in df
    assert "./configure" not in df


def test_make_build_passes_prefix():
    df = generate_dockerfile(_typed_recipe(BuildType.MAKE, **SRC))
    assert "make install PREFIX=/opt/demo prefix=/opt/demo" in df
    assert "./configure" not in df and "cmake -S" not in df


def test_autotools_bootstraps_configure_when_missing():
    df = generate_dockerfile(_typed_recipe(BuildType.AUTOTOOLS, configure_args=["--disable-x"], **SRC))
    assert "autoreconf -fi" in df and "autoconf" in df
    assert './configure --prefix=/opt/demo "--disable-x"' in df


def test_rust_cargo_uses_pinned_rust_builder_and_locked_install():
    from capsid.builders.dockerfile_gen import DEFAULT_RUST_BUILDER

    df = generate_dockerfile(_typed_recipe(BuildType.RUST_CARGO, cargo_args=["--features", "x"], **SRC))
    assert f"FROM {DEFAULT_RUST_BUILDER} AS source_builder" in df
    assert "@sha256:" in DEFAULT_RUST_BUILDER
    assert 'cargo install --locked --path . --root /opt/demo "--features" "x"' in df
    assert "FROM debian:bookworm-slim@sha256:" in df  # runtime stays the slim Debian image


def test_build_commands_override_the_default_steps():
    df = generate_dockerfile(_typed_recipe(BuildType.CMAKE, build_commands=["./build.sh /opt/demo"], **SRC))
    assert "RUN ./build.sh /opt/demo" in df
    assert "cmake -S ." not in df


BIN = {
    "binary_url_amd64": "https://example.org/demo-linux-x64.tar.gz",
    "sha256_amd64": "c" * 64,
    "binary_url_arm64": "https://example.org/demo-linux-arm64.tar.gz",
    "sha256_arm64": "d" * 64,
}


def test_binary_build_selects_artefact_per_arch_and_verifies_each():
    df = generate_dockerfile(_typed_recipe(BuildType.BINARY, **BIN))
    assert "ARG TARGETARCH" in df
    assert 'amd64) url="https://example.org/demo-linux-x64.tar.gz"; sum=' + "c" * 64 in df
    assert 'arm64) url="https://example.org/demo-linux-arm64.tar.gz"; sum=' + "d" * 64 in df
    assert 'echo "$sum  artefact" | sha256sum -c -' in df
    assert "build-essential" not in df.split("AS runtime")[0]


def test_binary_build_requires_url_and_checksum_for_every_platform():
    with pytest.raises(ValueError, match="binary_url_arm64"):
        generate_dockerfile(
            _typed_recipe(BuildType.BINARY, binary_url_amd64=BIN["binary_url_amd64"], sha256_amd64="c" * 64)
        )
    with pytest.raises(ValueError, match="sha256_amd64"):
        generate_dockerfile(
            _typed_recipe(
                BuildType.BINARY,
                binary_url_amd64=BIN["binary_url_amd64"],
                binary_url_arm64=BIN["binary_url_arm64"],
                sha256_arm64="d" * 64,
            )
        )


def test_binary_single_platform_only_needs_that_platform():
    from capsid.models import Platform

    recipe = _typed_recipe(BuildType.BINARY, binary_url_amd64=BIN["binary_url_amd64"], sha256_amd64="c" * 64)
    recipe.platforms = [Platform.LINUX_AMD64]
    df = generate_dockerfile(recipe)
    assert "arm64)" not in df


def test_test_file_paths_must_stay_inside_workdir():
    for bad in ("/etc/passwd", "../x", "a/../../b"):
        with pytest.raises(ValidationError):
            TestConfig(command="x", files={bad: "data"})
        with pytest.raises(ValidationError):
            TestConfig(command="x", output_file=bad)


def test_conda_env_is_built_at_its_runtime_path():
    """Conda packages embed their prefix: the env must not be relocated between stages."""
    import re

    from capsid.builders.dockerfile_gen import CONDA_PREFIX, PIXI_WORKSPACE

    r = _typed_recipe(BuildType.PIXI_CONDA, conda_packages=["samtools==1.25"])
    df = generate_dockerfile(r)
    assert CONDA_PREFIX.startswith(PIXI_WORKSPACE + "/")
    assert f"WORKDIR {PIXI_WORKSPACE}" in df
    copies = re.findall(r"^COPY --from=pixi_builder (\S+) (\S+)$", df, re.MULTILINE)
    env_copies = [(src, dst) for src, dst in copies if "envs" in src]
    assert env_copies == [(CONDA_PREFIX, CONDA_PREFIX)]
    assert f'CONDA_PREFIX="{CONDA_PREFIX}"' in df


def test_recipe_values_cannot_inject_dockerfile_or_shell():
    import pytest
    from pydantic import ValidationError

    df = generate_dockerfile(_typed_recipe(BuildType.AUTOTOOLS, configure_args=["--x; touch /pwned"], **SRC))
    assert '"--x; touch /pwned"' in df
    for bad in (
        {"dependencies": {"run": ["zlib1g \\\nRUN evil"]}},
        {"env": {"A=1\nRUN evil": "x"}},
        {"install_prefix": "/opt/x; rm -rf /"},
        {"install_prefix": "/opt/../etc"},
    ):
        with pytest.raises(ValidationError):
            _typed_recipe(BuildType.AUTOTOOLS, **SRC, **bad)
    r = _typed_recipe(BuildType.UV_PYPI, package="demo==1.0")
    r.requirements_lock = "demo==1.0\nEOF_REQUIREMENTS_LOCK\nRUN touch /pwned\n"
    with pytest.raises(ValueError, match="heredoc marker"):
        generate_dockerfile(r)
    r = _typed_recipe(BuildType.AUTOTOOLS, **SRC)
    r.name = "demo\nRUN evil"
    with pytest.raises(ValueError, match="not a valid recipe name"):
        generate_dockerfile(r)
