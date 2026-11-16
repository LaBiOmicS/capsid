"""
Unit tests for Capsid recipe linter and security auditor.
"""

from pathlib import Path
import tempfile
import yaml
from capsid.linter import lint_recipe, LintIssue


def test_lint_valid_recipe():
    recipe_dict = {
        "name": "fastp",
        "version": "1.4.0",
        "description": "Fast all-in-one FASTQ preprocessor",
        "homepage": "https://github.com/OpenGene/fastp",
        "license": "MIT",
        "platforms": ["linux/amd64", "linux/arm64"],
        "build": {
            "type": "pixi-conda",
            "conda_packages": ["fastp==1.4.0"]
        },
        "test": {
            "command": "fastp --help"
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(recipe_dict, f)
        p = Path(f.name)

    issues = lint_recipe(p)
    p.unlink()

    errors = [i for i in issues if i.level == "ERROR"]
    assert len(errors) == 0


def test_lint_detects_missing_test_command():
    recipe_dict = {
        "name": "badtool",
        "version": "1.0",
        "build": {
            "type": "pixi-conda",
            "conda_packages": ["badtool"]
        },
        "test": {
            "command": ""
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(recipe_dict, f)
        p = Path(f.name)

    issues = lint_recipe(p)
    p.unlink()

    errors = [i for i in issues if i.level == "ERROR"]
    assert any("Test command is empty" in e.message for e in errors)
