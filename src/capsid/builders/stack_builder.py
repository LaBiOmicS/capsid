"""
Stack Container Builder:
Combines multiple individual recipes into a single unified multi-tool container
(e.g., qc-stack with fastp + multiqc, or alignment-stack with bwa + samtools).
Uses the same hardened runtime stage as single-tool images (non-root, tini, HPC mounts).
"""

from typing import List, Tuple

from capsid.locking import pixi_manifest
from capsid.models import Platform, StackRecipe, Recipe, BuildType
from capsid.builders.dockerfile_gen import (
    CONDA_PREFIX,
    DEFAULT_DEBIAN_RUNTIME,
    DEFAULT_PIXI_BUILDER,
    DEFAULT_PYTHON_VERSION,
    DEFAULT_SOURCE_DATE_EPOCH,
    DEFAULT_UV_BUILDER,
    PIXI_BUILDER_TEMPLATE,
    UV_BUILDER_TEMPLATE,
    VENV_PREFIX,
    apt_setup,
    python_image,
    render,
    render_dockerfile,
)

DEFAULT_STACK_CHANNELS = ["conda-forge", "bioconda"]


def stack_platforms(resolved_recipes: List[Recipe]) -> List[Platform]:
    """Platforms supported by every component."""
    common = [Platform.LINUX_AMD64, Platform.LINUX_ARM64]
    for r in resolved_recipes:
        common = [p for p in common if p in r.platforms]
    return common


def stack_inputs(stack: StackRecipe, resolved_recipes: List[Recipe]):
    """(channels, conda_packages, pypi_packages, python_version) for a stack."""
    channels: List[str] = []
    conda: List[str] = []
    pypi: List[str] = []
    pyvers: List[str] = []
    for r in resolved_recipes:
        if r.build.type == BuildType.PIXI_CONDA:
            conda.extend(r.build.conda_packages or [])
            channels.extend(r.build.channels or DEFAULT_STACK_CHANNELS)
        elif r.build.type == BuildType.UV_PYPI and r.build.package:
            pypi.append(r.build.package)
            pyvers.append(r.build.python_version or DEFAULT_PYTHON_VERSION)
    pyvers = list(dict.fromkeys(pyvers))
    return (list(dict.fromkeys(channels)), list(dict.fromkeys(conda)), list(dict.fromkeys(pypi)),
            pyvers[0] if pyvers else DEFAULT_PYTHON_VERSION)


def generate_stack_dockerfile(stack: StackRecipe, resolved_recipes: List[Recipe]) -> str:
    """Generate a multi-stage Dockerfile combining all stack tools."""
    conda_packages: List[str] = []
    channels: List[str] = []
    pypi_packages: List[str] = []
    python_versions: List[str] = []
    build_deps: List[str] = []
    run_deps: List[str] = []

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
    builders: List[str] = []
    path_dirs: List[str] = []
    copies: List[Tuple[str, str, str]] = []
    provenance: List[Tuple[str, str, str]] = []

    if conda_packages:
        builders.append(render(
            PIXI_BUILDER_TEMPLATE,
            pixi_builder=DEFAULT_PIXI_BUILDER,
            source_date_epoch=DEFAULT_SOURCE_DATE_EPOCH,
            channels=list(dict.fromkeys(channels)),
            conda_packages=conda_packages,
            pixi_lock=stack.pixi_lock,
            pixi_manifest=pixi_manifest(stack.name, list(dict.fromkeys(channels)), conda_packages,
                                        stack_platforms(resolved_recipes)),
        ))
        path_dirs.append(f"{CONDA_PREFIX}/bin")
        copies.append(("pixi_builder", "/build/.pixi/envs/default", CONDA_PREFIX))
        provenance.append(("pixi_builder", "/build/pixi.lock", "/etc/capsid/pixi.lock"))

    runtime_base = DEFAULT_DEBIAN_RUNTIME
    if pypi_packages:
        runtime_base = python_image(python_versions[0])
        builders.append(render(
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
        ))
        path_dirs.append(f"{VENV_PREFIX}/bin")
        copies.append(("python_builder", VENV_PREFIX, VENV_PREFIX))
        provenance.append(("python_builder", "/requirements.lock", "/etc/capsid/requirements.lock"))

    if not builders:
        raise ValueError(f"Stack '{stack.name}' has no buildable components")

    labels = [
        ("org.opencontainers.image.title", stack.name),
        ("org.opencontainers.image.version", stack.version),
        ("org.opencontainers.image.description", stack.description or ""),
        ("org.opencontainers.image.vendor", "Capsid Project"),
        ("io.capsid.type", "compose"),
        ("io.capsid.components", ",".join(f"{r.name}@{r.version}" for r in resolved_recipes)),
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
    )
