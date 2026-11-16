"""
PyPI Scaffolder: inspects PyPI packages and generates uv-powered modern recipes.
"""

import httpx
from typing import Optional
from capsid.models import Recipe, BuildConfig, BuildType, TestConfig, Platform


def scaffold_from_pypi(package_name: str, version: Optional[str] = None) -> Recipe:
    """Fetch package metadata from PyPI and generate a modern recipe."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    if version:
        url = f"https://pypi.org/pypi/{package_name}/{version}/json"

    with httpx.Client(timeout=15.0) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            raise ValueError(f"Could not find package '{package_name}' on PyPI (HTTP {resp.status_code})")
        data = resp.json()

    info = data.get("info", {})
    resolved_version = info.get("version", "latest")
    description = info.get("summary") or f"{package_name} bioinformatics tool"
    homepage = info.get("home_page") or info.get("project_url") or f"https://pypi.org/project/{package_name}/"
    license_str = info.get("license") or "UNKNOWN"

    # Guess import name: usually lowercase with underscores instead of dashes
    import_guess = package_name.lower().replace("-", "_")

    # Command test
    test_cmd = f"python -c 'import {import_guess}; print(\"{package_name} OK\")'"

    # Check classifiers for categories
    categories = ["bioinformatics", "python"]
    for classifier in info.get("classifiers", []):
        if "Scientific/Engineering :: Bio-Informatics" in classifier:
            categories.append("genomics")
            break

    recipe = Recipe(
        name=package_name.lower(),
        version=resolved_version,
        description=description,
        homepage=homepage,
        license=license_str[:50] if license_str else "UNKNOWN",
        categories=sorted(list(set(categories))),
        platforms=[Platform.LINUX_AMD64, Platform.LINUX_ARM64],
        build=BuildConfig(
            type=BuildType.UV_PYPI,
            package=f"{package_name}=={resolved_version}",
            python_version="3.12",
            dependencies={"build": ["build-essential"], "run": []}
        ),
        test=TestConfig(
            command=test_cmd,
            expected_output=f"{package_name} OK"
        )
    )
    return recipe
