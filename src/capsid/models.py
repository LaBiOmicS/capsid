"""
Core data models for modern BioContainers recipes, builds, and catalog.
"""

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator


class BuildType(StrEnum):
    UV_PYPI = "uv-pypi"
    PIXI_CONDA = "pixi-conda"
    AUTOTOOLS = "autotools"
    CMAKE = "cmake"
    MAKE = "make"
    BINARY = "binary"
    RUST_CARGO = "rust-cargo"


# Strategies that compile or install from a downloaded archive (vs. a package solver)
SOURCE_BUILD_TYPES = (BuildType.AUTOTOOLS, BuildType.CMAKE, BuildType.MAKE, BuildType.RUST_CARGO)


class Platform(StrEnum):
    LINUX_AMD64 = "linux/amd64"
    LINUX_ARM64 = "linux/arm64"


APT_SNAPSHOT_RE = re.compile(r"\d{8}T\d{6}Z")
# JSON Schema mirrors of the field validators below (keep in sync)
APT_SNAPSHOT_PATTERN = r"^(\d{8}T\d{6}Z|latest)$"
SHA256_PATTERN = r"^\s*[0-9a-fA-F]{64}\s*$"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# Recipe names become image names and versions become tags (and both become directories)
NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
# Values that are written into the Dockerfile unquoted (package lists, paths, ENV/LABEL keys)
DEBIAN_PACKAGE_PATTERN = r"^[a-z0-9][a-z0-9+.-]*(:[a-z0-9]+)?(=[A-Za-z0-9.+~:-]+)?$"
INSTALL_PREFIX_PATTERN = r"^/[A-Za-z0-9._+-]+(/[A-Za-z0-9._+-]+)*$"
ENV_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
LABEL_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"


def _check_all(values, pattern: str, what: str):
    for v in values:
        if not re.fullmatch(pattern, v):
            raise ValueError(f"'{v}' is not a valid {what}")


def normalize_sha256(v: object) -> str | None:
    if v is None:
        return v
    if not isinstance(v, str):
        # YAML reads an all-digit checksum as a number (and drops leading zeros)
        raise ValueError("sha256 must be a quoted string in YAML, e.g. sha256: '0123...'")
    v = v.strip().lower()
    if not SHA256_RE.fullmatch(v):
        raise ValueError("sha256 must be 64 hexadecimal characters")
    return v


def validate_relative_path(v: str) -> str:
    """A path inside the test working directory: relative, no '..' components."""
    p = PurePosixPath(v)
    if not v or p.is_absolute() or ".." in p.parts:
        raise ValueError(f"'{v}' must be a relative path without '..'")
    return v


def validate_apt_snapshot(v: str | None) -> str | None:
    if v is None or v == "latest" or APT_SNAPSHOT_RE.fullmatch(v):
        return v
    raise ValueError("apt_snapshot must look like 20261115T000000Z or be 'latest'")


class BuildConfig(BaseModel):
    type: BuildType = Field(..., description="Build engine and strategy")
    # Python / PyPI options
    package: str | None = Field(None, description="PyPI requirement, e.g. 'cutadapt==5.2' (type: uv-pypi)")
    python_version: str | None = Field("3.12", description="Python version of the virtual environment (type: uv-pypi)")

    # Conda / Pixi options
    conda_packages: list[str] | None = Field(
        default_factory=list, description="Conda match specs, e.g. 'samtools==1.25' (type: pixi-conda)"
    )
    channels: list[str] | None = Field(
        default_factory=lambda: ["conda-forge", "bioconda"], description="Conda channels, in priority order"
    )

    # Source compilation & binary options
    source: str | None = Field(None, description="Source archive URL (types: autotools, cmake, make, rust-cargo)")
    sha256: str | None = Field(
        None,
        description="SHA-256 of the source archive; required with 'source'",
        json_schema_extra={"pattern": SHA256_PATTERN},
    )
    configure_args: list[str] | None = Field(
        default_factory=list, description="Extra arguments for ./configure (type: autotools)"
    )
    cmake_args: list[str] | None = Field(
        default_factory=list, description="Extra arguments for the cmake configure step (type: cmake)"
    )
    cargo_args: list[str] = Field(
        default_factory=list, description="Extra arguments for cargo install (type: rust-cargo)"
    )
    build_commands: list[str] | None = Field(
        default_factory=list,
        description="Shell commands that replace the default build steps, run in the unpacked source "
        "(source types and binary)",
    )
    install_prefix: str | None = Field(
        None, description="Installation prefix copied to the runtime image (default: /opt/<name>)"
    )
    binary_url_amd64: str | None = Field(
        None, description="Precompiled binary or archive URL for linux/amd64 (type: binary)"
    )
    binary_url_arm64: str | None = Field(
        None, description="Precompiled binary or archive URL for linux/arm64 (type: binary)"
    )
    sha256_amd64: str | None = Field(
        None, description="SHA256 of binary_url_amd64", json_schema_extra={"pattern": SHA256_PATTERN}
    )
    sha256_arm64: str | None = Field(
        None, description="SHA256 of binary_url_arm64", json_schema_extra={"pattern": SHA256_PATTERN}
    )

    # Dependencies
    dependencies: dict[str, list[str]] = Field(
        default_factory=lambda: {"build": list[str](), "run": list[str]()},
        description="Debian packages: 'build' installed in the builder stage, 'run' in the runtime image",
    )
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables set in the runtime image")

    # Reproducibility options
    apt_snapshot: str | None = Field(
        None,
        description="snapshot.debian.org timestamp (YYYYMMDDTHHMMSSZ) for apt packages; 'latest' disables "
        "the snapshot. Defaults to Capsid's pinned snapshot when base_image is not overridden",
        json_schema_extra={"pattern": APT_SNAPSHOT_PATTERN},
    )
    base_image: str | None = Field(
        None, description="Runtime (and source/uv builder) base image, ideally pinned by @sha256 digest"
    )
    source_date_epoch: int = Field(
        1700000000, description="SOURCE_DATE_EPOCH passed to BuildKit for deterministic image timestamps"
    )

    @field_validator("sha256", "sha256_amd64", "sha256_arm64", mode="before")
    @classmethod
    def _check_sha256(cls, v: object) -> str | None:
        return normalize_sha256(v)

    @field_validator("apt_snapshot")
    @classmethod
    def _check_apt_snapshot(cls, v: str | None) -> str | None:
        return validate_apt_snapshot(v)

    @field_validator("dependencies")
    @classmethod
    def _check_dependencies(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for packages in v.values():
            _check_all(packages, DEBIAN_PACKAGE_PATTERN, "Debian package name")
        return v

    @field_validator("env")
    @classmethod
    def _check_env(cls, v: dict[str, str]) -> dict[str, str]:
        _check_all(v, ENV_NAME_PATTERN, "environment variable name")
        return v

    @field_validator("install_prefix")
    @classmethod
    def _check_install_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            _check_all([v], INSTALL_PREFIX_PATTERN, "absolute install prefix")
            if ".." in v.split("/"):
                raise ValueError("install_prefix must not contain '..'")
        return v


class TestConfig(BaseModel):
    __test__ = False
    command: str = Field(..., description="Command to execute inside container to verify functionality")
    expected_output: str | None = Field(None, description="Substring expected in stdout/stderr")
    expected_exit_code: int = Field(0, description="Expected process exit code")
    timeout: int = Field(60, ge=1, description="Seconds before the test is aborted")

    # Scientific Golden Dataset & Replicability validation
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Input files written to the test working directory /test (relative path -> inline content)",
    )
    output_file: str | None = Field(None, description="File (relative to /test) the command must produce")
    expected_checksum: str | None = Field(
        None, description="Expected SHA-256 of output_file", json_schema_extra={"pattern": SHA256_PATTERN}
    )
    random_seed: int | None = Field(
        None, description="Exported as CAPSID_RANDOM_SEED and PYTHONHASHSEED for deterministic runs"
    )

    @field_validator("files")
    @classmethod
    def _check_files(cls, v: dict[str, str]) -> dict[str, str]:
        for name in v:
            validate_relative_path(name)
        return v

    @field_validator("output_file")
    @classmethod
    def _check_output_file(cls, v: str | None) -> str | None:
        return None if v is None else validate_relative_path(v)

    @field_validator("expected_checksum", mode="before")
    @classmethod
    def _check_checksum(cls, v: object) -> str | None:
        return normalize_sha256(v)

    @property
    def uses_workdir(self) -> bool:
        """Whether the test needs the /test working directory mounted."""
        return bool(self.files or self.output_file)


class Recipe(BaseModel):
    name: str = Field(
        ...,
        description="Tool name, lowercase; also the image name and recipes/<name>/ directory",
        json_schema_extra={"pattern": NAME_PATTERN},
    )
    version: str = Field(
        ...,
        description="Tool version; also the image tag and recipes/<name>/<version>/ directory",
        json_schema_extra={"pattern": VERSION_PATTERN},
    )
    description: str | None = Field(None, description="One-line summary of tool function")
    homepage: str | None = Field(None, description="Official project homepage or repository")
    license: str | None = Field(None, description="SPDX license identifier, e.g. 'MIT', 'GPL-3.0-or-later'")
    categories: list[str] = Field(
        default_factory=list, description="Topic/category tags (e.g. genomics, transcriptomics)"
    )

    # FAIR Principles & Persistent Identification
    doi: str | None = Field(None, description="Persistent Digital Object Identifier (DOI) for tool or paper")
    edam_topics: list[str] = Field(default_factory=list, description="EDAM ontology topic IDs, e.g. ['topic_0080']")
    edam_operations: list[str] = Field(
        default_factory=list, description="EDAM ontology operation IDs, e.g. ['operation_0573']"
    )
    ro_crate: dict[str, Any] | None = Field(
        None, description="Extra JSON-LD properties merged into the RO-Crate root Dataset (e.g. author, funder)"
    )

    platforms: list[Platform] = Field(
        default_factory=lambda: [Platform.LINUX_AMD64, Platform.LINUX_ARM64], description="Target architectures"
    )
    build: BuildConfig
    test: TestConfig
    labels: dict[str, str] = Field(default_factory=dict, description="Additional OCI image labels")

    # Lockfile contents loaded from sibling files (see capsid.locking); never serialized
    pixi_lock: str | None = Field(None, exclude=True, description="pixi.lock content")
    requirements_lock: str | None = Field(None, exclude=True, description="uv requirements.lock content")

    @field_validator("labels")
    @classmethod
    def _check_labels(cls, v: dict[str, str]) -> dict[str, str]:
        _check_all(v, LABEL_KEY_PATTERN, "label key")
        return v

    @property
    def canonical_tag(self) -> str:
        return f"{self.name}:{self.version}"


class StackComponent(BaseModel):
    recipe: str = Field(
        ...,
        description="Recipe name, as in recipes/<name>/<version>/recipe.yaml",
        json_schema_extra={"pattern": NAME_PATTERN},
    )
    version: str | None = Field(
        None,
        description="Recipe version (default: the highest version available)",
        json_schema_extra={"pattern": VERSION_PATTERN},
    )

    @field_validator("recipe")
    @classmethod
    def _check_recipe(cls, v: str) -> str:
        if not re.fullmatch(NAME_PATTERN, v):
            raise ValueError(f"'{v}' is not a recipe name (lowercase letters, digits, '.', '_', '-')")
        return v

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(VERSION_PATTERN, v):
            raise ValueError(f"'{v}' is not a valid version (letters, digits, '.', '_', '+', '-')")
        return v


class StackRecipe(BaseModel):
    name: str = Field(..., description="Stack container name, e.g. 'rnaseq-toolkit'")
    version: str = Field(..., description="Stack release version, e.g. '1.0.0'")
    description: str | None = Field(None, description="Description of the combined container")
    components: list[StackComponent] = Field(..., description="List of recipes included in this stack")
    test: TestConfig = Field(..., description="Test command verifying all tools work")
    apt_snapshot: str | None = Field(
        None,
        description="snapshot.debian.org timestamp or 'latest'",
        json_schema_extra={"pattern": APT_SNAPSHOT_PATTERN},
    )

    # Lockfile contents loaded from sibling files (see capsid.locking); never serialized
    pixi_lock: str | None = Field(None, exclude=True, description="pixi.lock content")
    requirements_lock: str | None = Field(None, exclude=True, description="uv requirements.lock content")

    @field_validator("apt_snapshot")
    @classmethod
    def _check_apt_snapshot(cls, v: str | None) -> str | None:
        return validate_apt_snapshot(v)

    @property
    def canonical_tag(self) -> str:
        return f"{self.name}:{self.version}"


def recipe_to_yaml(recipe: Recipe) -> str:
    """
    Serialize a recipe the way it is written to recipe.yaml: only the fields that differ
    from their defaults, plus the platforms (always explicit), in a stable field order.
    """
    import yaml

    data = recipe.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
    data.setdefault("platforms", [p.value for p in recipe.platforms])
    order = [
        "name",
        "version",
        "description",
        "homepage",
        "license",
        "categories",
        "doi",
        "edam_topics",
        "edam_operations",
        "platforms",
        "build",
        "test",
        "labels",
        "ro_crate",
    ]
    ordered = {k: data[k] for k in order if k in data}
    ordered.update({k: v for k, v in data.items() if k not in ordered})
    ordered.setdefault("build", {"type": recipe.build.type.value})
    ordered["build"].setdefault("type", recipe.build.type.value)
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)
