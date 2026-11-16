"""
Locating recipes by name and version (stack components, `name@version` CLI targets).
"""

import os
import re
from pathlib import Path


def recipe_search_dirs() -> list[Path]:
    """Recipe trees searched for name@version targets: $CAPSID_RECIPES_PATH (os.pathsep-separated) first."""
    extra = [Path(p) for p in os.environ.get("CAPSID_RECIPES_PATH", "").split(os.pathsep) if p]
    return [*extra, Path("recipes"), Path("capsid-hub/recipes"), Path("../capsid-hub/recipes")]


def _version_key(v: str) -> tuple:
    """Natural sort key: 1.9 < 1.25, numeric parts before text parts."""
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"[.\-_+]", v))


def find_recipe_candidate(tool_name: str, version: str | None = None) -> Path | None:
    """Search for existing recipe.yaml in standard recipe trees."""
    for base in recipe_search_dirs():
        if not base.exists():
            continue
        if version:
            candidate = base / tool_name / version / "recipe.yaml"
            if candidate.exists():
                return candidate
        else:
            # Versionless recipe, or the highest version
            candidate = base / tool_name / "recipe.yaml"
            if candidate.exists():
                return candidate
            tool_dir = base / tool_name
            versions = [d for d in tool_dir.glob("*") if (d / "recipe.yaml").is_file()] if tool_dir.is_dir() else []
            if versions:
                return max(versions, key=lambda d: _version_key(d.name)) / "recipe.yaml"
    return None
