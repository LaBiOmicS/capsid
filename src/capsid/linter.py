"""
Capsid Linter & Security Auditor: static checks for recipe.yaml and stack.yaml files.

Every rule has a stable code; ERRORs fail `capsid lint` (and the hub CI), WARNINGs do not.
The full list, with rationale, is documented in docs/security.md#recipe-linter.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from capsid.locking import CONDA_PLATFORMS, attach_lockfiles
from capsid.models import NAME_PATTERN, SOURCE_BUILD_TYPES, VERSION_PATTERN, BuildType, Platform, Recipe, StackRecipe
from capsid.resolver import find_recipe_candidate

console = Console()

ERROR = "ERROR"
WARNING = "WARNING"

NAME_RE = re.compile(NAME_PATTERN)
VERSION_RE = re.compile(VERSION_PATTERN)
SPDX_RE = re.compile(r"[A-Za-z0-9.+-]+(\s+(AND|OR|WITH)\s+[A-Za-z0-9.+-]+)*")
CONDA_SPEC_RE = re.compile(
    r"^\s*(?:[A-Za-z0-9_.\-]+::)?(?P<name>[A-Za-z0-9_.\-]+)\s*(?P<op>==|=)?\s*(?P<version>[^\s,<>=!*|]+)?"
)
PYPI_PIN_RE = re.compile(r"^\s*(?P<name>[A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*==\s*(?P<version>[^\s;,]+)")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
DEPENDENCY_GROUPS = ("build", "run")
TRIVIAL_TESTS = {"", "true", "false", ":", "echo", "exit 0", "exit"}

# Shell patterns that bypass the integrity guarantees of a recipe or weaken the image
DANGEROUS_PATTERNS: Sequence[tuple[str, re.Pattern]] = (
    ("download piped into a shell", re.compile(r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")),
    ("sudo", re.compile(r"(^|[\s;&|(])sudo\b")),
    ("world-writable permissions", re.compile(r"chmod\s+(-R\s+)?0?777\b")),
    ("eval", re.compile(r"(^|[\s;&|(])eval\b")),
    ("recursive delete of /", re.compile(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/(\s|$)")),
    (
        "disabled TLS verification",
        re.compile(r"(curl\b[^;&|]*\s(-k|--insecure)\b|wget\b[^;&|]*--no-check-certificate)"),
    ),
    ("unverified download", re.compile(r"\b(curl|wget)\b")),
)

# Fields that only have an effect for some build types
TYPE_SPECIFIC_FIELDS: dict[str, tuple[BuildType, ...]] = {
    "package": (BuildType.UV_PYPI,),
    "python_version": (BuildType.UV_PYPI,),
    "conda_packages": (BuildType.PIXI_CONDA,),
    "source": SOURCE_BUILD_TYPES,
    "sha256": SOURCE_BUILD_TYPES,
    "configure_args": (BuildType.AUTOTOOLS,),
    "cmake_args": (BuildType.CMAKE,),
    "cargo_args": (BuildType.RUST_CARGO,),
    "build_commands": (*SOURCE_BUILD_TYPES, BuildType.BINARY),
    "install_prefix": (*SOURCE_BUILD_TYPES, BuildType.BINARY),
    "binary_url_amd64": (BuildType.BINARY,),
    "binary_url_arm64": (BuildType.BINARY,),
    "sha256_amd64": (BuildType.BINARY,),
    "sha256_arm64": (BuildType.BINARY,),
}


@dataclass
class LintIssue:
    level: str  # ERROR or WARNING
    rule: str
    message: str
    file_path: Path

    def __str__(self) -> str:
        color = "red" if self.level == ERROR else "yellow"
        return f"[{color}][{self.level}][/{color}] {self.rule}: {self.message} ({self.file_path})"


class _Issues(list):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def error(self, rule: str, message: str) -> None:
        self.append(LintIssue(ERROR, rule, message, self.path))

    def warn(self, rule: str, message: str) -> None:
        self.append(LintIssue(WARNING, rule, message, self.path))


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------


def _load_yaml(path: Path, issues: _Issues) -> dict[str, Any] | None:
    if not path.is_file():
        issues.error("E001_FILE_NOT_FOUND", f"File '{path}' does not exist")
        return None
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        issues.error("E002_SCHEMA_INVALID", f"Invalid YAML: {e}")
        return None
    if not isinstance(raw, dict):
        issues.error("E002_SCHEMA_INVALID", "Top level must be a mapping")
        return None
    return raw


def _check_identity(name: str, version: str, issues: _Issues) -> None:
    if not NAME_RE.fullmatch(name):
        issues.error(
            "E005_INVALID_NAME",
            f"Name '{name}' must be lowercase and match {NAME_RE.pattern} (it becomes an image name)",
        )
    if not VERSION_RE.fullmatch(str(version)) or len(str(version)) > 128:
        issues.error(
            "E006_INVALID_VERSION", f"Version '{version}' must match {VERSION_RE.pattern} (it becomes an image tag)"
        )


def _check_test(test_command: str, issues: _Issues) -> None:
    if test_command.strip() in TRIVIAL_TESTS:
        issues.error(
            "E030_DUMMY_TEST", f"Test command '{test_command}' is empty or trivial; it must run the packaged tool"
        )


def _check_pixi_lock(lock: str | None, specs: Sequence[str], platforms: Sequence[Platform], issues: _Issues) -> None:
    if lock is None:
        issues.warn(
            "W014_MISSING_LOCKFILE",
            "No pixi.lock next to the definition; transitive conda dependencies will float. Run 'capsid lock'.",
        )
        return
    for spec in specs:
        m = CONDA_SPEC_RE.match(spec)
        if not m or m.group("op") != "==" or not m.group("version"):
            continue
        if f"/{m.group('name')}-{m.group('version')}-" not in lock:
            issues.error(
                "E016_STALE_LOCKFILE",
                f"pixi.lock does not contain {m.group('name')} {m.group('version')}; "
                "the build would fail (pixi install --locked). Run 'capsid lock'.",
            )
    for platform in platforms:
        if f"name: {CONDA_PLATFORMS[platform]}" not in lock and f"\n  {CONDA_PLATFORMS[platform]}:" not in lock:
            issues.error(
                "E017_LOCKFILE_PLATFORM",
                f"pixi.lock has no {CONDA_PLATFORMS[platform]} environment for {platform.value}. Run 'capsid lock'.",
            )


def _check_requirements_lock(lock: str | None, packages: Sequence[str], issues: _Issues) -> None:
    if lock is None:
        issues.warn(
            "W014_MISSING_LOCKFILE",
            "No requirements.lock next to the definition; transitive PyPI dependencies will float. Run 'capsid lock'.",
        )
        return
    for pkg in packages:
        m = PYPI_PIN_RE.match(pkg)
        if not m:
            continue
        name = re.sub(r"[-_.]+", "-", m.group("name")).lower()
        pattern = re.compile(
            rf"^{re.escape(name).replace('-', '[-_.]')}=={re.escape(m.group('version'))}\b",
            re.MULTILINE | re.IGNORECASE,
        )
        if not pattern.search(lock):
            issues.error(
                "E016_STALE_LOCKFILE",
                f"requirements.lock does not pin {m.group('name')}=={m.group('version')}; the build "
                "would install the locked version instead. Run 'capsid lock'.",
            )


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


def lint_recipe(recipe_path: Path, catalog_path: Path | None = None) -> list[LintIssue]:
    """Inspect a recipe file and return its errors and warnings."""
    issues = _Issues(recipe_path)
    raw = _load_yaml(recipe_path, issues)
    if raw is None:
        return list(issues)
    try:
        recipe = Recipe.model_validate(raw)
        attach_lockfiles(recipe, recipe_path)
    except (ValueError, OSError) as e:  # pydantic.ValidationError is a ValueError: report, don't crash
        issues.error("E002_SCHEMA_INVALID", f"Invalid recipe format: {e}")
        return list(issues)

    b = recipe.build
    raw_build = raw.get("build") or {}

    # Identity and layout
    _check_identity(recipe.name, recipe.version, issues)
    parts = recipe_path.resolve().parts
    if len(parts) >= 3 and parts[-1] == "recipe.yaml":
        if recipe.name != parts[-3]:
            issues.error(
                "E003_NAME_PATH_MISMATCH", f"Tool name '{recipe.name}' does not match directory name '{parts[-3]}'"
            )
        if str(recipe.version) != parts[-2]:
            issues.error(
                "E004_VERSION_PATH_MISMATCH",
                f"Recipe version '{recipe.version}' does not match version directory '{parts[-2]}'",
            )
    if not recipe.platforms:
        issues.error("E007_NO_PLATFORMS", "'platforms' must list at least one platform")
    elif len(set(recipe.platforms)) != len(recipe.platforms):
        issues.warn("W011_DUPLICATE_PLATFORM", "'platforms' lists a platform more than once")

    # Required inputs per build type
    if b.type == BuildType.UV_PYPI and not b.package:
        issues.error("E012_MISSING_BUILD_INPUT", "uv-pypi recipes require 'build.package'")
    if b.type == BuildType.PIXI_CONDA and not b.conda_packages:
        issues.error("E012_MISSING_BUILD_INPUT", "pixi-conda recipes require 'build.conda_packages'")
    if b.type in SOURCE_BUILD_TYPES:
        if not b.source:
            issues.error("E012_MISSING_BUILD_INPUT", f"{b.type.value} recipes require 'build.source'")
        elif not b.sha256:
            issues.error(
                "E011_MISSING_SHA256", "Source archives must declare 'build.sha256'; unverified downloads are refused"
            )
        if b.source and b.source.startswith("http://"):
            issues.warn("W013_INSECURE_HTTP", f"Plain HTTP source URL '{b.source}'. Use HTTPS.")
    if b.type == BuildType.BINARY:
        for platform in recipe.platforms:
            arch = "amd64" if platform == Platform.LINUX_AMD64 else "arm64"
            url, sha = getattr(b, f"binary_url_{arch}"), getattr(b, f"sha256_{arch}")
            if not url:
                issues.error(
                    "E012_MISSING_BUILD_INPUT", f"binary recipes for {platform.value} require 'build.binary_url_{arch}'"
                )
            elif not sha:
                issues.error("E011_MISSING_SHA256", f"'build.binary_url_{arch}' requires 'build.sha256_{arch}'")
            if url and url.startswith("http://"):
                issues.warn("W013_INSECURE_HTTP", f"Plain HTTP binary URL '{url}'. Use HTTPS.")

    # Fields set but ignored by this build type
    for field, types in TYPE_SPECIFIC_FIELDS.items():
        value = raw_build.get(field)
        if value not in (None, [], "", {}) and b.type not in types:
            if field == "python_version" and value == "3.12":
                continue  # the model default, often written out by `capsid init`
            issues.warn("W019_IGNORED_FIELD", f"'build.{field}' has no effect for build type '{b.type.value}'")

    if b.base_image and b.apt_snapshot not in (None, "latest"):
        issues.warn(
            "W019_IGNORED_FIELD",
            "'build.apt_snapshot' has no effect with 'build.base_image' (the snapshot is only configured "
            "on Capsid's default Debian images)",
        )

    # Python interpreter availability
    if b.type == BuildType.UV_PYPI and not b.base_image:
        from capsid.builders.dockerfile_gen import PYTHON_RUNTIME_IMAGES

        if (b.python_version or "3.12") not in PYTHON_RUNTIME_IMAGES:
            issues.error(
                "E013_UNSUPPORTED_PYTHON",
                f"python_version '{b.python_version}' has no pinned image "
                f"(supported: {', '.join(sorted(PYTHON_RUNTIME_IMAGES))}); set build.base_image to use another",
            )

    # Build commands
    for cmd in b.build_commands or []:
        for label, pattern in DANGEROUS_PATTERNS:
            if pattern.search(cmd):
                if label == "unverified download":
                    issues.warn(
                        "W010_DOWNLOAD_IN_BUILD_COMMAND",
                        f"build command downloads content that is not checksum-verified: '{cmd}'",
                    )
                else:
                    issues.error("E010_DANGEROUS_COMMAND", f"{label} in build command: '{cmd}'")
                break

    # Pinning and lockfiles
    if b.type == BuildType.PIXI_CONDA:
        for pkg in b.conda_packages or []:
            m = CONDA_SPEC_RE.match(pkg)
            if not m or not m.group("op") or not m.group("version"):
                issues.warn(
                    "W012_UNPINNED_PACKAGE",
                    f"Conda package '{pkg}' is not pinned; use e.g. '{pkg.split('=')[0]}=={recipe.version}'",
                )
        _check_pixi_lock(recipe.pixi_lock, b.conda_packages or [], recipe.platforms, issues)
    elif b.type == BuildType.UV_PYPI and b.package:
        if not PYPI_PIN_RE.match(b.package):
            issues.warn("W012_UNPINNED_PACKAGE", f"PyPI package '{b.package}' is not pinned with '=='")
        _check_requirements_lock(recipe.requirements_lock, [b.package], issues)

    # System packages and base image
    for group in b.dependencies:
        if group not in DEPENDENCY_GROUPS:
            issues.warn(
                "W018_UNKNOWN_DEPENDENCY_GROUP",
                f"build.dependencies.{group} is ignored (only 'build' and 'run' are used)",
            )
    if b.base_image and not DIGEST_RE.search(b.base_image):
        issues.warn(
            "W017_UNPINNED_BASE_IMAGE", f"base_image '{b.base_image}' is not pinned by digest (append @sha256:...)"
        )

    # Metadata
    if not recipe.license or recipe.license.upper() in ("UNKNOWN", "NONE", "NOASSERTION"):
        issues.warn("W020_MISSING_LICENSE", "License is missing; use an SPDX identifier (e.g. MIT, GPL-3.0-or-later)")
    elif not SPDX_RE.fullmatch(recipe.license.strip()):
        issues.warn("W022_INVALID_LICENSE", f"License '{recipe.license}' is not an SPDX identifier or expression")
    if not recipe.description or len(recipe.description.strip()) < 10:
        issues.warn("W021_SHORT_DESCRIPTION", "Description is empty or too short")
    if not recipe.homepage:
        issues.warn("W023_MISSING_HOMEPAGE", "Homepage is missing")

    # Test
    _check_test(recipe.test.command, issues)
    if recipe.test.expected_checksum and not recipe.test.output_file:
        issues.error("E015_CHECKSUM_WITHOUT_OUTPUT", "test.expected_checksum requires test.output_file")

    # Already published?
    if catalog_path and catalog_path.is_file():
        try:
            versions = json.loads(catalog_path.read_text()).get("tools", {}).get(recipe.name, {}).get("versions", {})
        except (OSError, ValueError):
            issues.warn("W041_CATALOG_UNREADABLE", f"Could not read catalog '{catalog_path}'")
        else:
            if str(recipe.version) in versions:
                issues.warn(
                    "W040_PUBLISHED_VERSION",
                    f"{recipe.name} {recipe.version} is already published; merging changes republishes "
                    "the same tag. Prefer a new version directory for functional changes.",
                )

    return list(issues)


# ---------------------------------------------------------------------------
# Stacks
# ---------------------------------------------------------------------------


def lint_stack(stack_path: Path) -> list[LintIssue]:
    """Inspect a stack file and return its errors and warnings."""
    from capsid.builders.stack_builder import stack_inputs, stack_platforms

    issues = _Issues(stack_path)
    raw = _load_yaml(stack_path, issues)
    if raw is None:
        return list(issues)
    try:
        stack = StackRecipe.model_validate(raw)
        attach_lockfiles(stack, stack_path)
    except (ValueError, OSError) as e:
        issues.error("E002_SCHEMA_INVALID", f"Invalid stack format: {e}")
        return list(issues)

    _check_identity(stack.name, stack.version, issues)
    if stack_path.suffix == ".yaml" and stack_path.stem != stack.name:
        issues.error(
            "E003_NAME_PATH_MISMATCH", f"Stack name '{stack.name}' does not match file name '{stack_path.name}'"
        )
    if not stack.components:
        issues.error("E050_EMPTY_STACK", "A stack needs at least one component")

    resolved: list[Recipe] = []
    seen = set()
    for comp in stack.components:
        if comp.recipe in seen:
            issues.error("E054_DUPLICATE_COMPONENT", f"Component '{comp.recipe}' is listed more than once")
        seen.add(comp.recipe)
        if not comp.version:
            issues.warn(
                "W051_UNPINNED_COMPONENT",
                f"Component '{comp.recipe}' has no version; the highest available version is used",
            )
        path = find_recipe_candidate(comp.recipe, comp.version)
        if not path:
            issues.error(
                "E051_COMPONENT_NOT_FOUND",
                f"Recipe '{comp.recipe}{'@' + comp.version if comp.version else ''}' not found "
                "(searched $CAPSID_RECIPES_PATH, recipes/, ../capsid-hub/recipes)",
            )
            continue
        try:
            recipe = Recipe.model_validate(yaml.safe_load(path.read_text()))
        except (ValueError, OSError, yaml.YAMLError) as e:
            issues.error("E051_COMPONENT_NOT_FOUND", f"Recipe '{path}' is invalid: {e}")
            continue
        if recipe.build.type not in (BuildType.PIXI_CONDA, BuildType.UV_PYPI):
            issues.error(
                "E052_UNSUPPORTED_COMPONENT",
                f"Component '{recipe.name}' uses build type '{recipe.build.type.value}'; "
                "stacks support pixi-conda and uv-pypi",
            )
            continue
        resolved.append(recipe)

    if resolved and len(resolved) == len(stack.components):
        if not stack_platforms(resolved):
            issues.error("E053_NO_COMMON_PLATFORM", "The components share no platform")
        try:
            _, conda, pypi, _ = stack_inputs(stack, resolved)
        except ValueError as e:
            issues.error("E055_INCOMPATIBLE_COMPONENTS", str(e))
        else:
            if conda:
                _check_pixi_lock(stack.pixi_lock, conda, stack_platforms(resolved), issues)
            if pypi:
                _check_requirements_lock(stack.requirements_lock, pypi, issues)

    _check_test(stack.test.command, issues)
    return list(issues)


def is_stack_file(path: Path) -> bool:
    """A YAML file that defines a stack (has 'components')."""
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(raw, dict) and "components" in raw


def lint_path(path: Path, catalog_path: Path | None = None) -> list[LintIssue]:
    """Lint a recipe or a stack file."""
    if path.name != "recipe.yaml" and is_stack_file(path):
        return lint_stack(path)
    return lint_recipe(path, catalog_path=catalog_path)
