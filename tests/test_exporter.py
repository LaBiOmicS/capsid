"""
Unit tests for Nextflow, Snakemake, and SIF exporters.
"""

from capsid.models import Recipe, BuildConfig, BuildType, TestConfig
from capsid.exporter import (
    export_nextflow_process,
    export_nextflow_config,
    export_snakemake_rule,
    export_apptainer_def
)


def get_sample_recipe() -> Recipe:
    return Recipe(
        name="samtools",
        version="1.25",
        description="Tools for SAM/BAM",
        build=BuildConfig(
            type=BuildType.PIXI_CONDA,
            conda_packages=["samtools==1.25"]
        ),
        test=TestConfig(command="samtools --version")
    )


def test_export_nextflow_process():
    recipe = get_sample_recipe()
    nf = export_nextflow_process(recipe)

    assert "process SAMTOOLS {" in nf
    assert "oras://ghcr.io/capsid-sif/samtools:1.25" in nf
    assert "ghcr.io/capsid/samtools:1.25" in nf
    assert "versions.yml" in nf
    assert "samtools: \"1.25\"" in nf


def test_export_snakemake_rule():
    recipe = get_sample_recipe()
    smk = export_snakemake_rule(recipe)

    assert "rule samtools:" in smk
    assert 'container:\n        "docker://ghcr.io/capsid/samtools:1.25"' in smk
    assert "samtools \\" in smk


def test_export_nextflow_config():
    recipe = get_sample_recipe()
    cfg = export_nextflow_config(recipe)

    assert "profiles {" in cfg
    assert "apptainer {" in cfg
    assert "singularity {" in cfg
    assert "docker {" in cfg
    assert "slurm {" in cfg
    assert "withName: 'SAMTOOLS' {" in cfg
    assert "container = 'ghcr.io/capsid/samtools:1.25'" in cfg


def test_export_apptainer_def():
    recipe = get_sample_recipe()
    app_def = export_apptainer_def(recipe)

    assert "Bootstrap: docker" in app_def
    assert "From: ghcr.io/capsid/samtools:1.25" in app_def
    assert "exec samtools" in app_def
