"""
JSON Schema (draft 2020-12) for recipe.yaml and stack.yaml, generated from the pydantic
models: used by editors (validation and completion) and by other tools that read recipes.
Published in the hub under schema/ by `capsid schema`.
"""

import json
from typing import Any

from pydantic import BaseModel

from capsid.models import Recipe, StackRecipe
from capsid.project import HUB_REPO

SCHEMA_BASE_URL = f"https://raw.githubusercontent.com/{HUB_REPO}/main/schema"
# In-memory fields (lockfile contents loaded from sibling files), not part of the YAML format
INTERNAL_FIELDS = ("pixi_lock", "requirements_lock")


def _schema(model: type[BaseModel], filename: str, title: str) -> dict[str, Any]:
    schema = model.model_json_schema(mode="validation")
    for field in INTERNAL_FIELDS:
        schema.get("properties", {}).pop(field, None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_BASE_URL}/{filename}",
        **{k: v for k, v in schema.items() if k != "title"},
        "title": title,
    }


def schemas() -> dict[str, dict[str, Any]]:
    """{filename: schema} for every published schema."""
    return {
        "recipe.schema.json": _schema(
            Recipe, "recipe.schema.json", "Capsid recipe (recipes/<tool>/<version>/recipe.yaml)"
        ),
        "stack.schema.json": _schema(StackRecipe, "stack.schema.json", "Capsid stack (stacks/<name>.yaml)"),
    }


def dumps(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"
