"""
Unit tests for catalog generator.
"""

from capsid.catalog import build_catalog_dict
from capsid.models import BuildConfig, BuildType, Recipe, TestConfig


def test_build_catalog_dict():
    recipes = [
        Recipe(
            name="samtools",
            version="1.25",
            description="SAM/BAM tools",
            license="MIT",
            categories=["genomics"],
            build=BuildConfig(type=BuildType.PIXI_CONDA, conda_packages=["samtools==1.25"]),
            test=TestConfig(command="samtools --version"),
        ),
        Recipe(
            name="cutadapt",
            version="5.2",
            description="Adapter trimmer",
            license="MIT",
            categories=["trimming"],
            build=BuildConfig(type=BuildType.UV_PYPI, package="cutadapt==5.2"),
            test=TestConfig(command="cutadapt --version"),
        ),
    ]

    cat = build_catalog_dict(recipes)
    assert cat["total_tools"] == 2
    assert cat["total_recipes"] == 2
    assert "samtools" in cat["tools"]
    assert "cutadapt" in cat["tools"]

    sam = cat["tools"]["samtools"]["versions"]["1.25"]
    assert sam["hpc_ready"] is True
    assert "container_uris" in sam
    assert "nextflow" in sam["container_uris"]
    assert "snakemake" in sam["container_uris"]
    assert "apptainer" in sam["container_uris"]
