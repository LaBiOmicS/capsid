"""
Core data models for modern BioContainers recipes, builds, and catalog.
"""

from enum import Enum
from typing import Dict, List, Optional, Any
import re
from pydantic import BaseModel, Field, field_validator


class BuildType(str, Enum):
    UV_PYPI = "uv-pypi"
    PIXI_CONDA = "pixi-conda"
    AUTOTOOLS = "autotools"
    CMAKE = "cmake"
    MAKE = "make"
    BINARY = "binary"
    RUST_CARGO = "rust-cargo"
    STACK = "stack"


class Platform(str, Enum):
    LINUX_AMD64 = "linux/amd64"
    LINUX_ARM64 = "linux/arm64"


APT_SNAPSHOT_RE = re.compile(r"\d{8}T\d{6}Z")


def validate_apt_snapshot(v: Optional[str]) -> Optional[str]:
    if v is None or v == "latest" or APT_SNAPSHOT_RE.fullmatch(v):
        return v
    raise ValueError("apt_snapshot must look like 20261115T000000Z or be 'latest'")


class BuildConfig(BaseModel):
    type: BuildType = Field(..., description="Build engine and strategy")
    # Python / PyPI options
    package: Optional[str] = Field(None, description="PyPI package specifier, e.g. 'cutadapt==4.9'")
    python_version: Optional[str] = Field("3.12", description="Python version to use")
    
    # Conda / Pixi options
    conda_packages: Optional[List[str]] = Field(default_factory=list, description="Conda packages for Pixi")
    channels: Optional[List[str]] = Field(default_factory=lambda: ["conda-forge", "bioconda"])

    # Source compilation & binary options
    source: Optional[str] = Field(None, description="Source archive or repository URL")
    sha256: Optional[str] = Field(None, description="SHA256 checksum of source archive")
    configure_args: Optional[List[str]] = Field(default_factory=list, description="Extra arguments for ./configure")
    cmake_args: Optional[List[str]] = Field(default_factory=list, description="Extra arguments for cmake")
    build_commands: Optional[List[str]] = Field(default_factory=list, description="Custom shell build commands")
    install_prefix: Optional[str] = Field(None, description="Destination prefix (default: /opt/<name>)")
    binary_url_amd64: Optional[str] = Field(None, description="Precompiled binary URL for x86_64")
    binary_url_arm64: Optional[str] = Field(None, description="Precompiled binary URL for aarch64")

    # Dependencies
    dependencies: Dict[str, List[str]] = Field(
        default_factory=lambda: {"build": [], "run": []},
        description="System apt/apk package dependencies separated by build-time and runtime"
    )
    env: Dict[str, str] = Field(default_factory=dict, description="Environment variables to set in container")

    # Reproducibility options
    apt_snapshot: Optional[str] = Field(
        None,
        description="snapshot.debian.org timestamp (YYYYMMDDTHHMMSSZ) for apt packages; 'latest' disables "
                    "the snapshot. Defaults to Capsid's pinned snapshot when base_image is not overridden",
    )
    base_image: Optional[str] = Field(None, description="Runtime (and source/uv builder) base image, ideally pinned by @sha256 digest")
    source_date_epoch: int = Field(1700000000, description="SOURCE_DATE_EPOCH passed to BuildKit for deterministic image timestamps")

    @field_validator("sha256")
    @classmethod
    def _check_sha256(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("sha256 must be 64 hexadecimal characters")
        return v

    @field_validator("apt_snapshot")
    @classmethod
    def _check_apt_snapshot(cls, v: Optional[str]) -> Optional[str]:
        return validate_apt_snapshot(v)


class TestConfig(BaseModel):
    __test__ = False
    command: str = Field(..., description="Command to execute inside container to verify functionality")
    expected_output: Optional[str] = Field(None, description="Substring expected in stdout/stderr")
    expected_exit_code: int = Field(0, description="Expected process exit code")

    # Scientific Golden Dataset & Replicability validation
    files: Dict[str, str] = Field(default_factory=dict, description="Test input files (filename -> inline content)")
    output_file: Optional[str] = Field(None, description="Output file path to verify checksum")
    expected_checksum: Optional[str] = Field(None, description="Expected SHA-256 checksum of generated output file")
    random_seed: Optional[int] = Field(None, description="Fixed random seed for deterministic execution")


class Recipe(BaseModel):
    name: str = Field(..., description="Canonical tool name, lowercase, e.g. 'samtools'")
    version: str = Field(..., description="Tool version, e.g. '1.21'")
    description: Optional[str] = Field(None, description="One-line summary of tool function")
    homepage: Optional[str] = Field(None, description="Official project homepage or repository")
    license: Optional[str] = Field(None, description="SPDX license identifier, e.g. 'MIT', 'GPL-3.0-or-later'")
    categories: List[str] = Field(default_factory=list, description="Topic/category tags (e.g. genomics, transcriptomics)")
    
    # FAIR Principles & Persistent Identification
    doi: Optional[str] = Field(None, description="Persistent Digital Object Identifier (DOI) for tool or paper")
    edam_topics: List[str] = Field(default_factory=list, description="EDAM ontology topic IDs, e.g. ['topic_0080']")
    edam_operations: List[str] = Field(default_factory=list, description="EDAM ontology operation IDs, e.g. ['operation_0573']")
    ro_crate: Optional[Dict[str, Any]] = Field(None, description="Custom RO-Crate metadata overlay")

    platforms: List[Platform] = Field(
        default_factory=lambda: [Platform.LINUX_AMD64, Platform.LINUX_ARM64],
        description="Target architectures"
    )
    build: BuildConfig
    test: TestConfig
    labels: Dict[str, str] = Field(default_factory=dict, description="Custom OCI labels")

    # Lockfile contents loaded from sibling files (see capsid.locking); never serialized
    pixi_lock: Optional[str] = Field(None, exclude=True, description="pixi.lock content")
    requirements_lock: Optional[str] = Field(None, exclude=True, description="uv requirements.lock content")

    @property
    def canonical_tag(self) -> str:
        return f"{self.name}:{self.version}"


class StackComponent(BaseModel):
    recipe: str = Field(..., description="Path or name of recipe to include")
    version: Optional[str] = Field(None, description="Override version if using catalog name")


class StackRecipe(BaseModel):
    name: str = Field(..., description="Stack container name, e.g. 'rnaseq-toolkit'")
    version: str = Field(..., description="Stack release version, e.g. '1.0.0'")
    description: Optional[str] = Field(None, description="Description of the combined container")
    components: List[StackComponent] = Field(..., description="List of recipes included in this stack")
    test: TestConfig = Field(..., description="Test command verifying all tools work")
    apt_snapshot: Optional[str] = Field(None, description="snapshot.debian.org timestamp or 'latest'")

    # Lockfile contents loaded from sibling files (see capsid.locking); never serialized
    pixi_lock: Optional[str] = Field(None, exclude=True, description="pixi.lock content")
    requirements_lock: Optional[str] = Field(None, exclude=True, description="uv requirements.lock content")

    @field_validator("apt_snapshot")
    @classmethod
    def _check_apt_snapshot(cls, v: Optional[str]) -> Optional[str]:
        return validate_apt_snapshot(v)

    @property
    def canonical_tag(self) -> str:
        return f"{self.name}:{self.version}"
