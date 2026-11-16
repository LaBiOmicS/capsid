"""
Integration tests for Capsid CLI commands using Typer CliRunner.
"""

from typer.testing import CliRunner

from capsid.main import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Capsid" in result.output and "lint" in result.output
    assert "build" in result.output
    assert "export" in result.output
    assert "lint" in result.output


def test_cli_export_help():
    result = runner.invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    assert "nextflow" in result.output
    assert "snakemake" in result.output
    assert "config" in result.output
    assert "def" in result.output


def test_cli_compose_help():
    result = runner.invoke(app, ["compose", "--help"])
    assert result.exit_code == 0
    assert "dockerfile" in result.output
    assert "build" in result.output
    assert "test" in result.output


def test_cli_stack_alias_help():
    result = runner.invoke(app, ["stack", "--help"])
    assert result.exit_code == 0
    assert "dockerfile" in result.output
    assert "build" in result.output
    assert "test" in result.output


def test_engine_version_matches_pyproject():
    import tomllib
    from pathlib import Path

    from capsid.project import VERSION

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == VERSION


def test_version_option():
    from typer.testing import CliRunner

    from capsid.main import app
    from capsid.project import VERSION

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"capsid {VERSION}"
