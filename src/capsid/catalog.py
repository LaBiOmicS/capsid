"""
Catalog generator: scans recipes, validates manifests, and compiles the central catalog.
Includes GA4GH TRS and workflow orchestrator (Nextflow, Snakemake, Singularity) metadata.
"""

import json
from pathlib import Path
from typing import Dict, List, Any
import yaml
from rich.console import Console
from rich.table import Table

from capsid.models import Recipe

console = Console()


def scan_recipes(recipes_dir: Path) -> List[Recipe]:
    """Scan directory recursively for all recipe.yaml files."""
    recipes: List[Recipe] = []
    if not recipes_dir.exists():
        return recipes

    for p in sorted(recipes_dir.rglob("recipe.yaml")):
        try:
            raw = yaml.safe_load(p.read_text())
            recipe = Recipe.model_validate(raw)
            recipes.append(recipe)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not parse {p}: {e}[/yellow]")

    return recipes


def build_catalog_dict(recipes: List[Recipe], registry_prefix: str = "ghcr.io/capsid") -> Dict[str, Any]:
    """Compile recipes list into a catalog structure compatible with GA4GH TRS and search UI."""
    tools: Dict[str, Any] = {}
    
    for r in recipes:
        if r.name not in tools:
            tools[r.name] = {
                "id": r.name,
                "name": r.name,
                "description": r.description or "",
                "homepage": r.homepage or "",
                "license": r.license or "UNKNOWN",
                "categories": r.categories,
                "versions": {}
            }
        
        tools[r.name]["versions"][r.version] = {
            "version": r.version,
            "canonical_tag": r.canonical_tag,
            "build_type": r.build.type.value,
            "platforms": [p.value for p in r.platforms],
            "test_command": r.test.command,
            "hpc_ready": True,
            "container_uris": {
                "docker": f"docker://{registry_prefix}/{r.name}:{r.version}",
                "singularity_oras": f"oras://ghcr.io/capsid-sif/{r.name}:{r.version}",
                "nextflow": f"{registry_prefix}/{r.name}:{r.version}",
                "snakemake": f"docker://{registry_prefix}/{r.name}:{r.version}"
            }
        }

    return {
        "version": "1.1.0",
        "total_tools": len(tools),
        "total_recipes": len(recipes),
        "tools": tools
    }


def export_catalog(recipes_dir: Path, output_file: Path) -> Dict[str, Any]:
    """Scan recipes, build catalog, and save to json."""
    recipes = scan_recipes(recipes_dir)
    cat = build_catalog_dict(recipes)
    output_file.write_text(json.dumps(cat, indent=2))
    return cat


def print_catalog_summary(recipes: List[Recipe]) -> None:
    """Print an elegant Rich summary table of all indexed recipes."""
    table = Table(title="Modern BioContainers Catalog (HPC & Orchestrator Ready)", show_header=True, header_style="bold magenta")
    table.add_column("Tool", style="bold green")
    table.add_column("Version", style="cyan")
    table.add_column("Engine", style="yellow")
    table.add_column("Platforms", style="blue")
    table.add_column("HPC / Workflows", style="bold green")
    table.add_column("License", style="dim")
    table.add_column("Description", style="white", max_width=35)

    for r in recipes:
        table.add_row(
            r.name,
            r.version,
            r.build.type.value,
            ", ".join([p.value.split('/')[1] for p in r.platforms]),
            "✔ NF / SMK / SIF",
            r.license or "UNKNOWN",
            (r.description or "")[:35]
        )

    console.print(table)
