"""
Scaffolding dispatcher: parses target string and creates appropriate modern recipe.
"""

from collections.abc import Callable

import httpx

from capsid.models import Recipe
from capsid.scaffolders.bioconda import scaffold_from_bioconda
from capsid.scaffolders.github import scaffold_from_github
from capsid.scaffolders.pypi import scaffold_from_pypi


def scaffold_recipe(target: str, version: str | None = None) -> Recipe:
    """
    Intelligently scaffolds a modern recipe from various sources:
    - pypi:cutadapt@4.9 or pypi:cutadapt
    - bioconda:samtools@1.19 or bioconda:bwa
    - github:samtools/samtools or https://github.com/samtools/samtools
    - cutadapt (auto-detects PyPI -> Bioconda)
    """
    clean = target.strip()
    target_version = version

    # Extract version if passed like name@1.19 or name==1.19
    if "@" in clean:
        clean, target_version = clean.split("@", 1)
    elif "==" in clean:
        clean, target_version = clean.split("==", 1)

    if clean.startswith("pypi:"):
        pkg = clean.split("pypi:", 1)[1]
        return scaffold_from_pypi(pkg, target_version)

    if clean.startswith("bioconda:"):
        pkg = clean.split("bioconda:", 1)[1]
        return scaffold_from_bioconda(pkg, target_version)

    if clean.startswith(("biocontainers:", "biocontainer:")):
        prefix = "biocontainers:" if clean.startswith("biocontainers:") else "biocontainer:"
        pkg = clean.split(prefix, 1)[1]
        recipe = scaffold_from_bioconda(pkg, target_version)
        recipe.categories = sorted({*recipe.categories, "biocontainers-migrated"})
        return recipe

    if clean.startswith("github:") or "github.com/" in clean:
        gh_target = clean.replace("github:", "")
        return scaffold_from_github(gh_target, target_version)

    # Auto-detection flow: PyPI, then Bioconda, then GitHub for owner/repo targets
    attempts: list[tuple[str, Callable[[str, str | None], Recipe]]] = [
        ("PyPI", scaffold_from_pypi),
        ("Bioconda", scaffold_from_bioconda),
    ]
    if "/" in clean and not clean.startswith("http"):
        attempts.append(("GitHub", scaffold_from_github))
    reasons = []
    for source_name, scaffold in attempts:
        try:
            return scaffold(clean, target_version)
        except (ValueError, httpx.HTTPError) as e:
            reasons.append(f"{source_name}: {e}")

    raise ValueError(
        f"Could not automatically resolve recipe for '{clean}' ({'; '.join(reasons)}). "
        f"Try explicit prefixes: 'pypi:{clean}', 'bioconda:{clean}', or 'github:owner/{clean}'"
    )
