"""
Exporter & Workflow Integrator:
Generates production-ready Nextflow DSL2 modules, Nextflow configuration profiles,
Snakemake rules, and Apptainer/Singularity definition files from Capsid recipes.
Templates: capsid/templates/exports/*.j2
"""

from capsid.models import Recipe
from capsid.project import ENGINE_URL, REGISTRY, VENDOR
from capsid.templating import render


def _nf_process_name(recipe: Recipe) -> str:
    return recipe.name.upper().replace("-", "_").replace(".", "_")


def export_nextflow_process(recipe: Recipe, registry_prefix: str = REGISTRY) -> str:
    """Generate an nf-core compliant Nextflow DSL2 process module."""
    return render(
        "exports/nextflow_process.nf.j2",
        recipe=recipe,
        proc_name=_nf_process_name(recipe),
        tool_cmd=recipe.name,
        registry_prefix=registry_prefix,
        engine_url=ENGINE_URL,
    )


def export_nextflow_config(recipe: Recipe | None = None, registry_prefix: str = REGISTRY) -> str:
    """Generate universal nextflow.config supporting Docker, Podman, Singularity, Apptainer, and SLURM."""
    return render(
        "exports/nextflow_config.j2",
        recipe=recipe,
        proc_name=_nf_process_name(recipe) if recipe else "",
        registry_prefix=registry_prefix,
    )


def export_snakemake_rule(recipe: Recipe, registry_prefix: str = REGISTRY) -> str:
    """Generate a production-ready Snakemake rule."""
    return render(
        "exports/snakemake_rule.smk.j2",
        recipe=recipe,
        rule_name=recipe.name.lower().replace("-", "_").replace(".", "_"),
        tool_cmd=recipe.name,
        registry_prefix=registry_prefix,
        engine_url=ENGINE_URL,
    )


def export_apptainer_def(recipe: Recipe, registry_prefix: str = REGISTRY) -> str:
    """Generate a native Singularity/Apptainer .def definition file."""
    return render("exports/apptainer.def.j2", recipe=recipe, registry_prefix=registry_prefix, vendor=VENDOR)
