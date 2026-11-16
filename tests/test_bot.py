"""Capsid robot: never overwrites existing recipes; cleans up rejected ones."""

import capsid.bot as bot
from capsid.models import BuildConfig, Recipe, TestConfig


def _fake(monkeypatch, test_command="seqkit version"):
    def scaffold(name, version=None):
        return Recipe(
            name=name,
            version=version or "2.8.2",
            description="FASTA/Q toolkit for testing",
            homepage="https://example.org",
            license="MIT",
            build=BuildConfig(type="pixi-conda", conda_packages=[f"{name}==2.8.2"]),
            test=TestConfig(command=test_command),
        )

    monkeypatch.setattr(bot, "scaffold_from_bioconda", scaffold)
    monkeypatch.setattr(bot, "fetch_quay_biocontainer_tag", lambda *a, **k: None)


def test_existing_recipe_is_never_touched(tmp_path, monkeypatch):
    _fake(monkeypatch, test_command="true")  # would fail lint if written
    existing = tmp_path / "seqkit" / "2.8.2" / "recipe.yaml"
    existing.parent.mkdir(parents=True)
    existing.write_text("hand-edited\n")
    c = bot.transform_candidate(bot.ToolCandidate(name="seqkit", version="2.8.2"), tmp_path)
    assert c.status == "SKIPPED"
    assert existing.read_text() == "hand-edited\n"


def test_rejected_recipe_is_removed_with_its_new_directories(tmp_path, monkeypatch):
    _fake(monkeypatch, test_command="true")
    c = bot.transform_candidate(bot.ToolCandidate(name="seqkit", version="2.8.2"), tmp_path)
    assert c.status == "FAILED"
    assert not (tmp_path / "seqkit").exists()


def test_valid_recipe_is_written(tmp_path, monkeypatch):
    _fake(monkeypatch)
    c = bot.transform_candidate(bot.ToolCandidate(name="seqkit", version="2.8.2"), tmp_path)
    assert c.status == "TRANSFORMED"
    assert (tmp_path / "seqkit" / "2.8.2" / "recipe.yaml").is_file()
