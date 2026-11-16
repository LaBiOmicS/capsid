#!/usr/bin/env python3
"""
Golden tests: the contract every Capsid implementation (Python today, Rust/Wasm maybe later)
must reproduce byte for byte.

    tests/golden/inputs/recipes/<tool>/<version>/{recipe.yaml, pixi.lock | requirements.lock}
    tests/golden/inputs/stacks/<stack>.yaml (+ <stack>.pixi.lock / .requirements.lock)
    tests/golden/expected/recipes/<tool>/<version>/{Dockerfile, module.nf, rule.smk,
                                                     Singularity.def, nextflow.config}
    tests/golden/expected/stacks/<stack>/Dockerfile
    tests/golden/expected/{catalog.json, nextflow.config}
    tests/golden/expected/schema/{recipe,stack}.schema.json  (also published in the hub)

    python scripts/update_golden.py --hub ../capsid-hub   # copy inputs from the hub, regenerate
    python scripts/update_golden.py                       # regenerate expected from current inputs
    python scripts/update_golden.py --check               # exit 1 if expected/ is stale

Regenerating is a deliberate act: review the diff of tests/golden/expected/ before committing.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"
INPUTS = GOLDEN / "inputs"
EXPECTED = GOLDEN / "expected"
LOCK_SUFFIXES = ("pixi.lock", "requirements.lock")


def _load_recipe(path: Path):
    from capsid.locking import attach_lockfiles
    from capsid.models import Recipe
    recipe = Recipe.model_validate(yaml.safe_load(path.read_text()))
    return attach_lockfiles(recipe, path)


def render_all(inputs: Path = INPUTS) -> Dict[str, str]:
    """Every expected file, keyed by its path relative to expected/."""
    from capsid.builders.dockerfile_gen import generate_dockerfile
    from capsid.builders.stack_builder import generate_stack_dockerfile
    from capsid.catalog import build_catalog_dict, scan_recipes
    from capsid.exporter import (export_apptainer_def, export_nextflow_config,
                                 export_nextflow_process, export_snakemake_rule)
    from capsid.locking import attach_lockfiles
    from capsid.models import StackRecipe

    out: Dict[str, str] = {}
    recipes_dir = inputs / "recipes"
    for path in sorted(recipes_dir.glob("*/*/recipe.yaml")):
        r = _load_recipe(path)
        base = f"recipes/{path.parent.parent.name}/{path.parent.name}"
        out[f"{base}/Dockerfile"] = generate_dockerfile(r)
        out[f"{base}/module.nf"] = export_nextflow_process(r)
        out[f"{base}/rule.smk"] = export_snakemake_rule(r)
        out[f"{base}/Singularity.def"] = export_apptainer_def(r)
        out[f"{base}/nextflow.config"] = export_nextflow_config(r)

    for path in sorted((inputs / "stacks").glob("*.yaml")):
        stack = StackRecipe.model_validate(yaml.safe_load(path.read_text()))
        attach_lockfiles(stack, path)
        comps = []
        for c in stack.components:
            if not c.version:
                raise SystemExit(f"{path.name}: golden stacks must pin component versions")
            comps.append(_load_recipe(recipes_dir / c.recipe / c.version / "recipe.yaml"))
        out[f"stacks/{stack.name}/Dockerfile"] = generate_stack_dockerfile(stack, comps)

    out["nextflow.config"] = export_nextflow_config()
    from capsid.schema import dumps, schemas
    for name, sch in schemas().items():
        out[f"schema/{name}"] = dumps(sch)
    out["catalog.json"] = json.dumps(build_catalog_dict(scan_recipes(recipes_dir)), indent=2) + "\n"
    return out


def copy_inputs(hub: Path) -> None:
    if INPUTS.exists():
        shutil.rmtree(INPUTS)
    for src in sorted((hub / "recipes").glob("*/*/recipe.yaml")):
        dst = INPUTS / "recipes" / src.parent.parent.name / src.parent.name
        dst.mkdir(parents=True)
        for f in src.parent.iterdir():
            if f.name == "recipe.yaml" or f.name in LOCK_SUFFIXES:
                shutil.copy2(f, dst / f.name)
    (INPUTS / "stacks").mkdir(parents=True)
    for f in sorted((hub / "stacks").iterdir()):
        if f.suffix == ".yaml" or f.name.endswith(LOCK_SUFFIXES):
            shutil.copy2(f, INPUTS / "stacks" / f.name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hub", type=Path, help="capsid-hub checkout to copy inputs from")
    ap.add_argument("--check", action="store_true", help="only compare; exit 1 if stale")
    args = ap.parse_args()

    if args.hub:
        copy_inputs(args.hub)
    rendered = render_all()
    current = {str(p.relative_to(EXPECTED)): p.read_text()
               for p in EXPECTED.rglob("*") if p.is_file()} if EXPECTED.exists() else {}
    stale = sorted(k for k in rendered.keys() | current.keys() if rendered.get(k) != current.get(k))
    for k in stale:
        print(("+ " if k not in current else "- " if k not in rendered else "~ ") + k)
    if args.check:
        return 1 if stale else 0
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    for rel, content in rendered.items():
        p = EXPECTED / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    print(f"{len(rendered)} golden files written ({len(stale)} changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
