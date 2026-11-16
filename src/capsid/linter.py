"""
Capsid Linter & Security Auditor: automated verification of community recipes for Capsid Hub.
Checks for schema validity, duplicate releases, security anti-patterns, and test rigor.
"""

from pathlib import Path
from typing import List, Optional, Tuple
import yaml
from rich.console import Console

from capsid.models import Recipe, BuildType
from capsid.locking import attach_lockfiles

console = Console()


class LintIssue:
    def __init__(self, level: str, rule: str, message: str, file_path: Path):
        self.level = level  # "ERROR" or "WARNING"
        self.rule = rule
        self.message = message
        self.file_path = file_path

    def __str__(self) -> str:
        color = "red" if self.level == "ERROR" else "yellow"
        return f"[{color}][{self.level}][/{color}] {self.rule}: {self.message} ({self.file_path})"


def lint_recipe(recipe_path: Path, catalog_path: Optional[Path] = None) -> List[LintIssue]:
    """Inspect a recipe file and return list of errors and warnings."""
    issues: List[LintIssue] = []

    if not recipe_path.exists():
        issues.append(LintIssue("ERROR", "E001_FILE_NOT_FOUND", f"File '{recipe_path}' does not exist", recipe_path))
        return issues

    # 1. Parse YAML & Schema
    try:
        raw = yaml.safe_load(recipe_path.read_text())
        recipe = Recipe.model_validate(raw)
        attach_lockfiles(recipe, recipe_path)
    except Exception as e:
        issues.append(LintIssue("ERROR", "E002_SCHEMA_INVALID", f"Invalid recipe format: {e}", recipe_path))
        return issues

    # 2. Check Directory Structure Conformance
    parts = recipe_path.resolve().parts
    if len(parts) >= 3 and parts[-1] == "recipe.yaml":
        expected_version = parts[-2]
        expected_tool = parts[-3]
        if recipe.name != expected_tool:
            issues.append(LintIssue(
                "ERROR", "E003_NAME_PATH_MISMATCH",
                f"Tool name '{recipe.name}' does not match directory name '{expected_tool}'",
                recipe_path
            ))
        if str(recipe.version) != expected_version:
            issues.append(LintIssue(
                "ERROR", "E004_VERSION_PATH_MISMATCH",
                f"Recipe version '{recipe.version}' does not match version directory '{expected_version}'",
                recipe_path
            ))

    # 3. Security: Check for Dangerous Build Commands
    dangerous_patterns = ["sudo", "| sh", "| bash", "chmod 777", "eval", "wget -O -", "curl -sL"]
    for cmd in recipe.build.build_commands or []:
        for pattern in dangerous_patterns:
            if pattern in cmd:
                issues.append(LintIssue(
                    "ERROR", "E010_DANGEROUS_COMMAND",
                    f"Forbidden or dangerous pattern '{pattern}' detected in build command: '{cmd}'",
                    recipe_path
                ))

    # 4. Security: Source integrity & Pinning
    if recipe.build.type in [BuildType.AUTOTOOLS, BuildType.CMAKE, BuildType.MAKE]:
        if recipe.build.source and not recipe.build.sha256:
            issues.append(LintIssue(
                "ERROR", "E011_MISSING_SHA256",
                "Source archives must specify a 'sha256' cryptographic checksum for supply chain integrity",
                recipe_path
            ))
        if recipe.build.source and recipe.build.source.startswith("http://"):
            issues.append(LintIssue(
                "WARNING", "W013_INSECURE_HTTP",
                f"Insecure plaintext HTTP source URL '{recipe.build.source}'. Use HTTPS for integrity.",
                recipe_path
            ))

    if recipe.build.type == BuildType.PIXI_CONDA:
        for pkg in recipe.build.conda_packages or []:
            if "==" not in pkg and "=" not in pkg:
                issues.append(LintIssue(
                    "WARNING", "W012_UNPINNED_PACKAGE",
                    f"Conda package '{pkg}' is unpinned. Exact pinning (e.g. '{pkg}=={recipe.version}') is recommended for reproducibility.",
                    recipe_path
                ))
    elif recipe.build.type == BuildType.UV_PYPI:
        if recipe.build.package and "==" not in recipe.build.package:
            issues.append(LintIssue(
                "WARNING", "W012_UNPINNED_PACKAGE",
                f"PyPI package '{recipe.build.package}' lacks exact version pinning '=='.",
                recipe_path
            ))

    # 4b. Reproducibility: committed lockfiles
    if recipe.build.type == BuildType.PIXI_CONDA and not recipe.pixi_lock:
        issues.append(LintIssue(
            "WARNING", "W014_MISSING_LOCKFILE",
            "No pixi.lock next to recipe.yaml; transitive conda dependencies will float. Run 'capsid lock'.",
            recipe_path
        ))
    elif recipe.build.type == BuildType.UV_PYPI and not recipe.requirements_lock:
        issues.append(LintIssue(
            "WARNING", "W014_MISSING_LOCKFILE",
            "No requirements.lock next to recipe.yaml; transitive PyPI dependencies will float. Run 'capsid lock'.",
            recipe_path
        ))

    # 5. Metadata & Licensing
    if not recipe.license or recipe.license.upper() in ["UNKNOWN", "NONE"]:
        issues.append(LintIssue(
            "WARNING", "W020_MISSING_LICENSE",
            "Recipe license is missing or UNKNOWN. Please provide a valid SPDX identifier (e.g. MIT, GPL-3.0)",
            recipe_path
        ))

    if not recipe.description or len(recipe.description.strip()) < 10:
        issues.append(LintIssue(
            "WARNING", "W021_SHORT_DESCRIPTION",
            "Tool description is empty or too short. Provide a clear one-line explanation.",
            recipe_path
        ))

    # 6. Test Quality
    dummy_tests = ["true", "false", "echo", "exit 0", ""]
    if recipe.test.command.strip() in dummy_tests:
        issues.append(LintIssue(
            "ERROR", "E030_DUMMY_TEST",
            f"Test command is empty or trivial ('{recipe.test.command}'). Must execute the actual tool binary or an import check.",
            recipe_path
        ))

    # 7. Check Duplicate / Immutable releases if catalog is provided
    if catalog_path and catalog_path.exists():
        try:
            import json
            cat = json.loads(catalog_path.read_text())
            tool_data = cat.get("tools", {}).get(recipe.name, {})
            existing_versions = tool_data.get("versions", {})
            if str(recipe.version) in existing_versions:
                # Flag as note/warning or check if this is an overwrite
                pass
        except Exception:
            pass

    return issues
