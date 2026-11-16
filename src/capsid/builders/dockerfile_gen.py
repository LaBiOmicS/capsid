"""
Dockerfile generator: multi-stage, hardened Dockerfiles for every build strategy.

Every image shares one runtime stage (templates/dockerfile/runtime.j2): non-root user bio:1000,
tini as PID 1, universal HPC mount points, and FAIR provenance assets in /etc/capsid/.

Reproducibility guarantees:
  * base and builder images are pinned by digest;
  * apt resolves against a fixed snapshot.debian.org timestamp (default images only);
  * conda/PyPI closures come from committed lockfiles (pixi.lock / requirements.lock with
    hashes) when present -- `capsid lock` generates them; without a lock the solver runs at
    build time and the resolved set is still recorded in /etc/capsid/;
  * source archives are verified against their sha256 before building.
"""

import json
import re

import yaml

from capsid.exporter import export_nextflow_process, export_snakemake_rule
from capsid.locking import pixi_manifest
from capsid.models import NAME_PATTERN, VERSION_PATTERN, BuildType, Platform, Recipe
from capsid.project import HUB_URL, VENDOR, VERSION
from capsid.ro_crate import generate_ro_crate
from capsid.sbom import generate_spdx_sbom
from capsid.templating import render

# Images pinned by digest (tag kept for readability). Update with:
#   python scripts/bump_pins.py   (or: docker buildx imagetools inspect <image>:<tag>)
# Dockerfile frontend (the `# syntax=` parser image) -- also part of the build input
DOCKERFILE_FRONTEND = "docker/dockerfile:1.4@sha256:9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc"
DEFAULT_DEBIAN_RUNTIME = "debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc"
DEFAULT_PIXI_BUILDER = (
    "ghcr.io/prefix-dev/pixi:0.84.2@sha256:8201f134655a8261f382ee565ddf1498e5f215887bb14e812e1f47c09f4f7196"
)
# Rust toolchain for rust-cargo recipes (Debian bookworm, like the runtime)
DEFAULT_RUST_BUILDER = (
    "rust:1.100.0-slim-bookworm@sha256:7c7d4eb4d05b7781187a327809466cfddf29753f6defdc3d19a0e291951b7a37"
)
DEFAULT_UV_BUILDER = (
    "ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85"
)
# Official Python images used as builder AND runtime for uv-pypi recipes, so the venv's
# interpreter (/usr/local/bin/python3.X) exists in both stages.
PYTHON_RUNTIME_IMAGES = {
    "3.10": "python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370",
    "3.11": "python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39",
    "3.12": "python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df",
    "3.13": "python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72",
}
DEFAULT_PYTHON_VERSION = "3.12"

# snapshot.debian.org timestamp for apt. Must be newer than the base images above
# (bump together with the digests to pick up security updates).
DEFAULT_APT_SNAPSHOT = "20261115T000000Z"
DEBIAN_SUITE = "bookworm"
DEFAULT_SOURCE_DATE_EPOCH = 1700000000

VENV_PREFIX = "/opt/venv"
# Conda packages embed their absolute install prefix (shebangs, pkg-config files, compiled-in
# paths), so the environment is installed at its final path and copied to the same path.
PIXI_WORKSPACE = "/opt/pixi"
CONDA_PREFIX = f"{PIXI_WORKSPACE}/.pixi/envs/default"
# Stable, short alias of the conda environment in the runtime image
CONDA_ENV_LINK = "/opt/conda-env"

# Base PATH used in /etc/environment, which pam_env reads literally (no $PATH expansion).
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


# Builder templates (templates/dockerfile/), one per build strategy
UV_BUILDER_TEMPLATE = "dockerfile/uv_builder.j2"
PIXI_BUILDER_TEMPLATE = "dockerfile/pixi_builder.j2"
SOURCE_BUILDER_TEMPLATE = "dockerfile/source_builder.j2"
RUNTIME_TEMPLATE = "dockerfile/runtime.j2"
APT_SNAPSHOT_TEMPLATE = "dockerfile/apt_snapshot.j2"


# apt packages every source builder needs, plus the toolchain of each strategy
SOURCE_BASE_PACKAGES = ["ca-certificates", "curl", "tar", "gzip", "bzip2", "xz-utils"]
SOURCE_TOOLCHAINS = {
    BuildType.AUTOTOOLS: ["build-essential", "autoconf", "automake", "libtool", "pkg-config"],
    BuildType.MAKE: ["build-essential", "pkg-config"],
    BuildType.CMAKE: ["build-essential", "cmake", "pkg-config"],
    BuildType.RUST_CARGO: ["build-essential", "pkg-config"],
    BuildType.BINARY: ["unzip"],
}
# Docker TARGETARCH for each platform
TARGET_ARCH = {Platform.LINUX_AMD64: "amd64", Platform.LINUX_ARM64: "arm64"}


def binary_artefacts(recipe: Recipe) -> list[dict[str, str]]:
    """[{arch, url, sha256}] for every platform of a binary recipe."""
    b = recipe.build
    by_arch = {
        Platform.LINUX_AMD64: (b.binary_url_amd64, b.sha256_amd64),
        Platform.LINUX_ARM64: (b.binary_url_arm64, b.sha256_arm64),
    }
    out = []
    for platform in recipe.platforms:
        url, sha = by_arch[platform]
        arch = TARGET_ARCH[platform]
        if not url:
            raise ValueError(
                f"Recipe '{recipe.name}': binary build for {platform.value} requires 'build.binary_url_{arch}'"
            )
        if not sha:
            raise ValueError(
                f"Recipe '{recipe.name}': refusing to install unverified binary '{url}'. Add 'build.sha256_{arch}'."
            )
        out.append({"arch": arch, "url": url, "sha256": sha})
    return out


def python_image(version: str) -> str:
    """Digest-pinned python:X-slim-bookworm image for a Python version."""
    try:
        return PYTHON_RUNTIME_IMAGES[version]
    except KeyError:
        raise ValueError(
            f"Unsupported python_version '{version}'. Supported: {sorted(PYTHON_RUNTIME_IMAGES)} "
            "(or set build.base_image to an image that provides it)"
        ) from None


def apt_setup(snapshot: str | None, base_overridden: bool) -> str:
    """apt snapshot snippet; empty for custom base images or snapshot='latest'."""
    if base_overridden or snapshot == "latest":
        return ""
    return render(APT_SNAPSHOT_TEMPLATE, snapshot=snapshot or DEFAULT_APT_SNAPSHOT, suite=DEBIAN_SUITE).rstrip()


def render_dockerfile(
    *,
    header: str,
    builders: list[str],
    runtime_base: str,
    source_date_epoch: int,
    run_deps: list[str],
    path_dirs: list[str],
    copies: list[tuple[str, str, str]],
    provenance_copies: list[tuple[str, str, str]],
    labels: list[tuple[str, str]],
    apt: str,
    fair_assets: list[dict[str, str]] | None = None,
    extra_env: dict[str, str] | None = None,
    links: list[tuple[str, str]] | None = None,
) -> str:
    """Assemble builder stage(s) + the shared hardened runtime stage."""
    runtime = render(
        RUNTIME_TEMPLATE,
        runtime_base=runtime_base,
        source_date_epoch=source_date_epoch,
        apt_setup=apt,
        run_deps=run_deps,
        path=":".join(path_dirs),
        system_path=SYSTEM_PATH,
        copies=copies,
        copy_targets=[c[2] for c in copies],
        provenance_copies=provenance_copies,
        fair_assets=fair_assets or [],
        extra_env=sorted((extra_env or {}).items()),
        links=links or [],
        labels=labels,
    )
    parts = [f"# syntax={DOCKERFILE_FRONTEND}\n" + header.rstrip() + "\n"]
    parts.extend(b.rstrip() + "\n" for b in builders)
    parts.append(runtime)
    return "\n".join(parts)


def _fair_assets(recipe: Recipe) -> list[dict[str, str]]:
    recipe_yaml = yaml.dump(recipe.model_dump(mode="json", exclude_none=True), sort_keys=False).strip()
    return [
        {"marker": "EOF_RECIPE", "filename": "recipe.yaml", "content": recipe_yaml},
        {
            "marker": "EOF_SBOM",
            "filename": "sbom.spdx.json",
            "content": json.dumps(generate_spdx_sbom(recipe), indent=2),
        },
        {
            "marker": "EOF_ROCRATE",
            "filename": "ro-crate-metadata.json",
            "content": json.dumps(generate_ro_crate(recipe), indent=2),
        },
        {"marker": "EOF_NF", "filename": "module.nf", "content": export_nextflow_process(recipe).strip()},
        {"marker": "EOF_SMK", "filename": "rule.smk", "content": export_snakemake_rule(recipe).strip()},
    ]


def _recipe_labels(recipe: Recipe) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = [
        ("org.opencontainers.image.title", recipe.name),
        ("org.opencontainers.image.version", recipe.version),
        ("org.opencontainers.image.description", recipe.description or ""),
        ("org.opencontainers.image.url", recipe.homepage or ""),
        ("org.opencontainers.image.licenses", recipe.license or "UNKNOWN"),
        ("org.opencontainers.image.vendor", VENDOR),
        # GHCR links the package to this repository (and its permissions) through this label
        ("org.opencontainers.image.source", HUB_URL),
    ]
    if recipe.doi:
        labels.append(("org.opencontainers.image.doi", recipe.doi))
    labels += [
        ("io.capsid.version", VERSION),
        ("io.capsid.engine", recipe.build.type.value),
        ("io.capsid.fair.ro_crate", "/etc/capsid/ro-crate-metadata.json"),
        ("io.capsid.fair.sbom", "/etc/capsid/sbom.spdx.json"),
        ("io.capsid.fair.module_nf", "/etc/capsid/module.nf"),
        ("io.capsid.fair.rule_smk", "/etc/capsid/rule.smk"),
    ]
    if recipe.edam_topics:
        labels.append(("io.capsid.fair.edam_topics", ",".join(recipe.edam_topics)))
    if recipe.edam_operations:
        labels.append(("io.capsid.fair.edam_operations", ",".join(recipe.edam_operations)))
    labels += [
        ("io.capsid.hpc", "apptainer,singularity,slurm"),
        ("io.capsid.orchestrators", "nextflow,snakemake"),
    ]
    for k, v in recipe.labels.items():
        labels.append((k, v))
    return labels


def check_identity(name: str, version: str, base_image: str | None = None) -> None:
    """
    Name, version and base image are written into the Dockerfile and image references unquoted.
    The model accepts any string (so `capsid lint` can report E005/E006 precisely); rendering does not.
    """
    if not re.fullmatch(NAME_PATTERN, name):
        raise ValueError(f"'{name}' is not a valid recipe name ({NAME_PATTERN})")
    if not re.fullmatch(VERSION_PATTERN, str(version)):
        raise ValueError(f"'{version}' is not a valid version ({VERSION_PATTERN})")
    if base_image is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*", base_image):
        raise ValueError(f"'{base_image}' is not a valid image reference")


def generate_dockerfile(recipe: Recipe) -> str:
    """Render the optimized, hardened, FAIR Dockerfile for a single recipe."""
    b = recipe.build
    check_identity(recipe.name, recipe.version, b.base_image)
    python_version = b.python_version or DEFAULT_PYTHON_VERSION
    if b.base_image:
        runtime_base = b.base_image
    elif b.type == BuildType.UV_PYPI:
        runtime_base = python_image(python_version)
    else:
        runtime_base = DEFAULT_DEBIAN_RUNTIME
    sde = b.source_date_epoch or DEFAULT_SOURCE_DATE_EPOCH
    apt = apt_setup(b.apt_snapshot, base_overridden=bool(b.base_image))
    build_deps = list(b.dependencies.get("build", []))
    run_deps = list(b.dependencies.get("run", []))

    links: list[tuple[str, str]] = []
    env: dict[str, str] = {}
    if b.type == BuildType.UV_PYPI:
        if not b.package:
            raise ValueError(f"Recipe '{recipe.name}': uv-pypi builds require 'build.package'")
        header = "# Multi-stage hardened Python container generated by Capsid (uv)"
        builders = [
            render(
                UV_BUILDER_TEMPLATE,
                uv_builder=DEFAULT_UV_BUILDER,
                builder_base=runtime_base,
                source_date_epoch=sde,
                apt_setup=apt,
                venv_prefix=VENV_PREFIX,
                python_version=python_version,
                build_deps=build_deps,
                pypi_packages=[b.package],
                requirements_lock=recipe.requirements_lock,
            )
        ]
        path_dirs = [f"{VENV_PREFIX}/bin"]
        copies = [("python_builder", VENV_PREFIX, VENV_PREFIX)]
        provenance = [("python_builder", "/requirements.lock", "/etc/capsid/requirements.lock")]

    elif b.type == BuildType.PIXI_CONDA:
        if not b.conda_packages:
            raise ValueError(f"Recipe '{recipe.name}': pixi-conda builds require 'build.conda_packages'")
        header = "# Multi-stage hardened Conda container generated by Capsid via pixi"
        channels = b.channels or ["conda-forge", "bioconda"]
        builders = [
            render(
                PIXI_BUILDER_TEMPLATE,
                pixi_builder=DEFAULT_PIXI_BUILDER,
                workspace=PIXI_WORKSPACE,
                source_date_epoch=sde,
                channels=channels,
                conda_packages=b.conda_packages,
                pixi_lock=recipe.pixi_lock,
                pixi_manifest=pixi_manifest(recipe.name, channels, b.conda_packages, recipe.platforms),
            )
        ]
        path_dirs = [f"{CONDA_PREFIX}/bin"]
        copies = [("pixi_builder", CONDA_PREFIX, CONDA_PREFIX)]
        links = [(CONDA_PREFIX, CONDA_ENV_LINK)]
        env = {"CONDA_PREFIX": CONDA_PREFIX}
        provenance = [("pixi_builder", f"{PIXI_WORKSPACE}/pixi.lock", "/etc/capsid/pixi.lock")]

    else:
        prefix = b.install_prefix or f"/opt/{recipe.name}"
        binaries: list[dict[str, str]] = []
        if b.type == BuildType.BINARY:
            binaries = binary_artefacts(recipe)
            header = "# Hardened container for a precompiled, checksum-verified binary generated by Capsid"
        else:
            if not b.source:
                raise ValueError(f"Recipe '{recipe.name}': {b.type.value} builds require 'build.source'")
            if not b.sha256:
                raise ValueError(
                    f"Recipe '{recipe.name}': refusing to build unverified source '{b.source}'. Add 'build.sha256'."
                )
            header = "# Multi-stage compilation container generated by Capsid"
        if b.type == BuildType.RUST_CARGO:
            builder_base = DEFAULT_RUST_BUILDER
            builder_apt = apt_setup(b.apt_snapshot, base_overridden=False)
        else:
            builder_base, builder_apt = runtime_base, apt
        builders = [
            render(
                SOURCE_BUILDER_TEMPLATE,
                build_type=b.type.value,
                tool_name=recipe.name,
                builder_base=builder_base,
                source_date_epoch=sde,
                apt_setup=builder_apt,
                apt_packages=list(dict.fromkeys(SOURCE_BASE_PACKAGES + SOURCE_TOOLCHAINS[b.type] + build_deps)),
                source=b.source or "",
                sha256=b.sha256 or "",
                binaries=binaries,
                prefix=prefix,
                build_commands=b.build_commands or [],
                configure_args=b.configure_args or [],
                cmake_args=b.cmake_args or [],
                cargo_args=b.cargo_args,
            )
        ]
        path_dirs = [f"{prefix}/bin"]
        copies = [("source_builder", prefix, prefix)]
        provenance = []

    return render_dockerfile(
        header=header,
        builders=builders,
        runtime_base=runtime_base,
        source_date_epoch=sde,
        run_deps=run_deps,
        path_dirs=path_dirs,
        copies=copies,
        provenance_copies=provenance,
        labels=_recipe_labels(recipe),
        apt=apt,
        fair_assets=_fair_assets(recipe),
        extra_env={**env, **b.env},
        links=links,
    )
