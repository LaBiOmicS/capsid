"""
Unit tests for Capsid data models.
"""

import pytest
from capsid.models import Recipe, BuildConfig, BuildType, TestConfig, Platform


def test_recipe_model_validation():
    recipe_dict = {
        "name": "samtools",
        "version": "1.25",
        "description": "Tools for SAM/BAM",
        "homepage": "https://github.com/samtools/samtools",
        "license": "MIT",
        "categories": ["genomics"],
        "platforms": ["linux/amd64", "linux/arm64"],
        "build": {
            "type": "pixi-conda",
            "conda_packages": ["samtools==1.25"]
        },
        "test": {
            "command": "samtools --version",
            "expected_output": "samtools 1.25"
        }
    }

    recipe = Recipe.model_validate(recipe_dict)
    assert recipe.name == "samtools"
    assert recipe.version == "1.25"
    assert recipe.canonical_tag == "samtools:1.25"
    assert recipe.build.type == BuildType.PIXI_CONDA
    assert recipe.build.conda_packages == ["samtools==1.25"]
    assert recipe.test.command == "samtools --version"
    assert recipe.test.expected_exit_code == 0
    assert len(recipe.platforms) == 2


def test_uv_pypi_recipe_model():
    recipe = Recipe(
        name="cutadapt",
        version="5.2",
        build=BuildConfig(
            type=BuildType.UV_PYPI,
            package="cutadapt==5.2",
            python_version="3.12"
        ),
        test=TestConfig(command="cutadapt --version")
    )
    assert recipe.canonical_tag == "cutadapt:5.2"
    assert recipe.build.package == "cutadapt==5.2"
