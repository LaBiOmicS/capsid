"""
Stack Container Builder:
Combines multiple individual recipes into a single unified multi-tool container
(e.g., qc-stack with fastp + multiqc, or alignment-stack with bwa + samtools).
Uses the same hardened runtime stage as single-tool images (non-root, tini, HPC mounts).
"""

import json

import yaml

from capsid.builders.dockerfile_gen import (
    CONDA_ENV_LINK,
    CONDA_PREFIX,
    DEFAULT_DEBIAN_RUNTIME,
    DEFAULT_PIXI_BUILDER,
    DEFAULT_PYTHON_VERSION,
    DEFAULT_SOURCE_DATE_EPOCH,
    DEFAULT_UV_BUILDER,
    PIXI_BUILDER_TEMPLATE,
    PIXI_WORKSPACE,
    UV_BUILDER_TEMPLATE,
    VENV_PREFIX,
    apt_setup,
    check_identity,
    python_image,
    render,
    render_dockerfile,
)
from capsid.locking import pixi_manifest
from capsid.models import BuildType, Platform, Recipe, StackRecipe
from capsid.project import HUB_URL, VENDOR, VERSION
from capsid.sbom import generate_stack_sbom

DEFAULT_STACK_CHANNELS = ["conda-forge", "bioconda"]


def stack_platforms(resolved_recipes: list[Recipe]) -> list[Platform]:
    """Platforms supported by every component."""
    common = [Platform.LINUX_AMD64, Platform.LINUX_ARM64]
    for r in resolved_recipes:
        common = [p for p in common if p in r.platforms]
    return common


def stack_inputs(stack: StackRecipe, resolved_recipes: list[Recipe]):
    """(channels, conda_packages, pypi_packages, python_version) for a stack."""
    channels: list[str] = []
    conda: list[str] = []
    pypi: list[str] = []
    pyvers: list[str] = []
    for r in resolved_recipes:
        if r.build.type == BuildType.PIXI_CONDA:
            conda.extend(r.build.conda_packages or [])
            channels.extend(r.build.channels or DEFAULT_STACK_CHANNELS)
        elif r.build.type == BuildType.UV_PYPI:
            if r.build.package:
                pypi.append(r.build.package)
            pyvers.append(r.build.python_version or DEFAULT_PYTHON_VERSION)
        else:
            raise ValueError(
                f"Stack '{stack.name}': component '{r.name}' uses build type '{r.build.type.value}'; "
                "stacks support pixi-conda and uv-pypi"
            )
    pyvers = list(dict.fromkeys(pyvers))
    if len(pyvers) > 1:
        raise ValueError(
            f"Stack '{stack.name}': PyPI components require different Python versions {pyvers}; "
            "they must share one interpreter"
        )
    return (
        list(dict.fromkeys(channels)),
        list(dict.fromkeys(conda)),
        list(dict.fromkeys(pypi)),
        pyvers[0] if pyvers else DEFAULT_PYTHON_VERSION,
    )


def generate_stack_dockerfile(stack: StackRecipe, resolved_recipes: list[Recipe]) -> str:
    """Generate a multi-stage Dockerfile combining all stack tools."""
    check_identity(stack.name, stack.version)
    conda_packages: list[str] = []
    channels: list[str] = []
    pypi_packages: list[str] = []
    python_versions: list[str] = []
    build_deps: list[str] = []
    run_deps: list[str] = []

    for r in resolved_recipes:
        if r.build.type == BuildType.PIXI_CONDA:
            conda_packages.extend(r.build.conda_packages or [])
            channels.extend(r.build.channels or DEFAULT_STACK_CHANNELS)
        elif r.build.type == BuildType.UV_PYPI:
            if r.build.package:
                pypi_packages.append(r.build.package)
                python_versions.append(r.build.python_version or DEFAULT_PYTHON_VERSION)
                build_deps.extend(r.build.dependencies.get("build", []))
        else:
            raise ValueError(
                f"Stack '{stack.name}': component '{r.name}' uses build type "
                f"'{r.build.type.value}', which stacks do not support yet"
            )
        run_deps.extend(r.build.dependencies.get("run", []))

    conda_packages = list(dict.fromkeys(conda_packages))
    pypi_packages = list(dict.fromkeys(pypi_packages))
    python_versions = list(dict.fromkeys(python_versions))
    if len(python_versions) > 1:
        raise ValueError(
            f"Stack '{stack.name}': PyPI components require different Python versions "
            f"{python_versions}; they must share one interpreter"
        )

    apt = apt_setup(stack.apt_snapshot, base_overridden=False)
    builders: list[str] = []
    path_dirs: list[str] = []
    copies: list[tuple[str, str, str]] = []
    provenance: list[tuple[str, str, str]] = []
    links: list[tuple[str, str]] = []
    env: dict[str, str] = {}

    if conda_packages:
        builders.append(
            render(
                PIXI_BUILDER_TEMPLATE,
                pixi_builder=DEFAULT_PIXI_BUILDER,
                workspace=PIXI_WORKSPACE,
                source_date_epoch=DEFAULT_SOURCE_DATE_EPOCH,
                channels=list(dict.fromkeys(channels)),
                conda_packages=conda_packages,
                pixi_lock=stack.pixi_lock,
                pixi_manifest=pixi_manifest(
                    stack.name, list(dict.fromkeys(channels)), conda_packages, stack_platforms(resolved_recipes)
                ),
            )
        )
        path_dirs.append(f"{CONDA_PREFIX}/bin")
        copies.append(("pixi_builder", CONDA_PREFIX, CONDA_PREFIX))
        links.append((CONDA_PREFIX, CONDA_ENV_LINK))
        env["CONDA_PREFIX"] = CONDA_PREFIX
        provenance.append(("pixi_builder", f"{PIXI_WORKSPACE}/pixi.lock", "/etc/capsid/pixi.lock"))

    runtime_base = DEFAULT_DEBIAN_RUNTIME
    if pypi_packages:
        runtime_base = python_image(python_versions[0])
        builders.append(
            render(
                UV_BUILDER_TEMPLATE,
                uv_builder=DEFAULT_UV_BUILDER,
                builder_base=runtime_base,
                source_date_epoch=DEFAULT_SOURCE_DATE_EPOCH,
                apt_setup=apt,
                venv_prefix=VENV_PREFIX,
                python_version=python_versions[0],
                build_deps=list(dict.fromkeys(build_deps)),
                pypi_packages=pypi_packages,
                requirements_lock=stack.requirements_lock,
            )
        )
        path_dirs.append(f"{VENV_PREFIX}/bin")
        copies.append(("python_builder", VENV_PREFIX, VENV_PREFIX))
        provenance.append(("python_builder", "/requirements.lock", "/etc/capsid/requirements.lock"))

    if not builders:
        raise ValueError(f"Stack '{stack.name}' has no buildable components")

    labels = [
        ("org.opencontainers.image.title", stack.name),
        ("org.opencontainers.image.version", stack.version),
        ("org.opencontainers.image.description", stack.description or ""),
        ("org.opencontainers.image.vendor", VENDOR),
        ("org.opencontainers.image.source", HUB_URL),
        ("io.capsid.version", VERSION),
        ("io.capsid.type", "stack"),
        ("io.capsid.components", ",".join(f"{r.name}@{r.version}" for r in resolved_recipes)),
        ("io.capsid.fair.sbom", "/etc/capsid/sbom.spdx.json"),
        ("io.capsid.hpc", "apptainer,singularity,slurm"),
        ("io.capsid.orchestrators", "nextflow,snakemake"),
    ]

    return render_dockerfile(
        header="# Unified Multi-tool Stack Container generated by Capsid",
        builders=builders,
        runtime_base=runtime_base,
        source_date_epoch=DEFAULT_SOURCE_DATE_EPOCH,
        run_deps=list(dict.fromkeys(run_deps)),
        path_dirs=path_dirs,
        copies=copies,
        provenance_copies=provenance,
        labels=labels,
        apt=apt,
        extra_env=env,
        links=links,
        fair_assets=[
            {
                "marker": "EOF_STACK",
                "filename": "stack.yaml",
                "content": yaml.safe_dump(stack.model_dump(mode="json", exclude_none=True), sort_keys=False).strip(),
            },
            {
                "marker": "EOF_SBOM",
                "filename": "sbom.spdx.json",
                "content": json.dumps(
                    generate_stack_sbom(stack, resolved_recipes, DEFAULT_SOURCE_DATE_EPOCH), indent=2
                ),
            },
        ],
    )
