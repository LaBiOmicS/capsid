"""
Bioconda Scaffolder / Migrator: imports legacy meta.yaml recipes from Bioconda and modernizes them.
"""

import re

import httpx
import yaml

from capsid.models import BuildConfig, BuildType, Platform, Recipe, TestConfig


def clean_jinja_yaml(text: str) -> tuple[str, dict[str, str]]:
    """Extract Jinja2 {% set var = "value" %} variables and substitute into YAML string."""
    variables: dict[str, str] = {}

    # Extract set variables
    for line in text.splitlines():
        match = re.search(r"{%\s*set\s+([a-zA-Z0-9_]+)\s*=\s*[\"']?([^\"'%]+)[\"']?\s*%}", line)
        if match:
            k, v = match.group(1).strip(), match.group(2).strip()
            variables[k] = v

    # Replace simple Jinja placeholders {{ var }}
    cleaned = text
    for k, v in variables.items():
        cleaned = re.sub(rf"\{{\{{\s*{k}\s*\}}\}}", v, cleaned)

    # Remove unhandled jinja blocks like {{ compiler('c') }}
    cleaned = re.sub(r"\{%.*?%\}", "", cleaned)
    cleaned = re.sub(r"\{\{.*?\}\}", "unresolved", cleaned)

    return cleaned, variables


def scaffold_from_bioconda(package_name: str, version: str | None = None) -> Recipe:
    """Fetch meta.yaml from Bioconda GitHub repository and convert to modern Recipe."""
    # Bioconda stores recipes at recipes/{package}/meta.yaml
    url = f"https://raw.githubusercontent.com/bioconda/bioconda-recipes/master/recipes/{package_name}/meta.yaml"

    with httpx.Client(timeout=15.0) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            raise ValueError(f"Could not find Bioconda recipe for '{package_name}' at {url} (HTTP {resp.status_code})")
        raw_text = resp.text

    cleaned_text, vars_dict = clean_jinja_yaml(raw_text)

    try:
        data = yaml.safe_load(cleaned_text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Could not parse the Bioconda recipe for '{package_name}': {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected Bioconda recipe format for '{package_name}'")

    pkg_info = data.get("package", {})
    name = pkg_info.get("name") or vars_dict.get("name") or package_name
    resolved_version = str(version or pkg_info.get("version") or vars_dict.get("version") or "1.0.0")

    about = data.get("about", {})
    summary = about.get("summary") or f"{name} bioinformatics tool"
    homepage = about.get("home") or f"https://bioconda.github.io/recipes/{name}/README.html"
    license_str = str(about.get("license") or "UNKNOWN")

    # Test info
    test_data = data.get("test", {})
    test_commands = test_data.get("commands", [])
    test_cmd = test_commands[0] if isinstance(test_commands, list) and len(test_commands) > 0 else f"{name} --version"

    # Check if this is a Python tool or Conda tool
    build_dict = data.get("build", {})
    reqs = data.get("requirements", {})
    run_reqs = reqs.get("run", []) if isinstance(reqs, dict) else []

    is_python = build_dict.get("noarch") == "python" or any("python" in str(r) for r in run_reqs)

    # Check if package is actually accessible on PyPI
    has_pypi = False
    if is_python:
        try:
            with httpx.Client(timeout=3.0) as client:
                has_pypi = client.head(f"https://pypi.org/pypi/{name}/json").status_code == 200
        except httpx.HTTPError:
            has_pypi = False  # PyPI unreachable: fall back to the conda package

    if is_python and has_pypi:
        # Preferred: modernize to uv-pypi if package exists on PyPI
        build_cfg = BuildConfig(type=BuildType.UV_PYPI, package=f"{name}=={resolved_version}", python_version="3.12")
    else:
        # Use Pixi with Bioconda / Conda-Forge channels for 100% compatibility and instant build!
        build_cfg = BuildConfig(
            type=BuildType.PIXI_CONDA,
            conda_packages=[f"{name}=={resolved_version}"],
            channels=["conda-forge", "bioconda"],
        )

    return Recipe(
        name=name.lower(),
        version=resolved_version,
        description=summary,
        homepage=homepage,
        license=license_str,
        categories=["bioinformatics", "bioconda-migrated"],
        platforms=[Platform.LINUX_AMD64, Platform.LINUX_ARM64],
        build=build_cfg,
        test=TestConfig(command=test_cmd, expected_output=None),
    )
