"""
Unit tests for Capsid data models.
"""

import pytest

from capsid.models import BuildConfig, BuildType, Recipe, TestConfig


def test_recipe_model_validation():
    recipe_dict = {
        "name": "samtools",
        "version": "1.25",
        "description": "Tools for SAM/BAM",
        "homepage": "https://github.com/samtools/samtools",
        "license": "MIT",
        "categories": ["genomics"],
        "platforms": ["linux/amd64", "linux/arm64"],
        "build": {"type": "pixi-conda", "conda_packages": ["samtools==1.25"]},
        "test": {"command": "samtools --version", "expected_output": "samtools 1.25"},
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
        build=BuildConfig(type=BuildType.UV_PYPI, package="cutadapt==5.2", python_version="3.12"),
        test=TestConfig(command="cutadapt --version"),
    )
    assert recipe.canonical_tag == "cutadapt:5.2"
    assert recipe.build.package == "cutadapt==5.2"


def test_ro_crate_overlay_merges_into_root_and_adds_entities():
    from capsid.models import BuildConfig, BuildType, Recipe, TestConfig
    from capsid.ro_crate import generate_ro_crate

    def recipe(overlay):
        return Recipe(
            name="t",
            version="1",
            build=BuildConfig(type=BuildType.PIXI_CONDA, conda_packages=["t==1"]),
            test=TestConfig(command="t -h"),
            ro_crate=overlay,
        )

    crate = generate_ro_crate(
        recipe(
            {
                "author": [{"@id": "https://orcid.org/0000-0002-1825-0097"}],
                "@graph": [{"@id": "https://orcid.org/0000-0002-1825-0097", "@type": "Person", "name": "J. Carberry"}],
            }
        )
    )
    root = next(e for e in crate["@graph"] if e["@id"] == "./")
    assert root["author"] == [{"@id": "https://orcid.org/0000-0002-1825-0097"}]
    assert any(e.get("@type") == "Person" for e in crate["@graph"])
    with pytest.raises(ValueError, match="hasPart"):
        generate_ro_crate(recipe({"hasPart": []}))
    with pytest.raises(ValueError, match="@graph"):
        generate_ro_crate(recipe({"@graph": [{"name": "no id"}]}))


def test_numeric_sha256_is_rejected_with_a_hint():
    import pytest
    from pydantic import ValidationError

    from capsid.models import BuildConfig

    with pytest.raises(ValidationError, match="quoted string"):
        BuildConfig(
            type="make",
            source="https://e.org/x.tgz",
            sha256=1111111111111111111111111111111111111111111111111111111111111111,
        )
