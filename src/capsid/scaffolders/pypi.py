"""
PyPI Scaffolder: inspects PyPI packages and generates uv-powered modern recipes.
"""

import httpx

from capsid.models import BuildConfig, BuildType, Platform, Recipe, TestConfig

# Trove classifiers -> SPDX identifiers (most common scientific licenses)
CLASSIFIER_SPDX = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "CC0-1.0",
}


def spdx_license(info: dict) -> str:
    """Best SPDX identifier from PyPI metadata: license_expression, a short license field, or classifiers."""
    expr = (info.get("license_expression") or "").strip()
    if expr:
        return expr
    short = (info.get("license") or "").strip()
    if short and "\n" not in short and len(short) <= 40 and " " not in short.strip():
        return short
    for classifier in info.get("classifiers", []):
        if classifier in CLASSIFIER_SPDX:
            return CLASSIFIER_SPDX[classifier]
    return "UNKNOWN"


def scaffold_from_pypi(package_name: str, version: str | None = None) -> Recipe:
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
    license_str = spdx_license(info)

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
        license=license_str,
        categories=sorted(set(categories)),
        platforms=[Platform.LINUX_AMD64, Platform.LINUX_ARM64],
        build=BuildConfig(
            type=BuildType.UV_PYPI,
            package=f"{package_name}=={resolved_version}",
            python_version="3.12",
            dependencies={"build": ["build-essential"], "run": []},
        ),
        test=TestConfig(command=test_cmd, expected_output=f"{package_name} OK"),
    )
    return recipe
