"""
Capsid Syncer: downloads and synchronizes recipes and catalog from upstream Capsid Hub.
Allows researchers to keep their local catalog up-to-date with a single command (like apt update / brew update).
"""

from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from capsid.catalog import export_catalog, scan_recipes
from capsid.project import HUB_REPO

console = Console()

DEFAULT_UPSTREAM_REPO = HUB_REPO


def sync_hub(
    upstream_repo: str = DEFAULT_UPSTREAM_REPO,
    recipes_dir: Path = Path("recipes"),
    catalog_path: Path = Path("catalog.json"),
) -> tuple[int, int]:
    """
    Synchronizes local recipe tree and catalog.json with upstream Capsid Hub.
    Returns (num_new_recipes, total_recipes).
    """
    console.print(f"[bold cyan]🔄 Synchronizing local catalog with [green]{upstream_repo}[/green]...[/bold cyan]")

    url = f"https://raw.githubusercontent.com/{upstream_repo}/main/catalog.json"
    remote_data: dict[str, Any] = {}

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                remote_data = resp.json()
            else:
                console.print(
                    f"[yellow]Notice: Could not fetch remote catalog (HTTP {resp.status_code}). "
                    "Checking local recipes.[/yellow]"
                )
    except (httpx.HTTPError, ValueError) as e:
        console.print(
            f"[yellow]Notice: Network connection to GitHub unavailable: {e}. Reindexing local catalog.[/yellow]"
        )

    # Download missing recipes if remote catalog has items
    added_count = 0
    if remote_data and "tools" in remote_data:
        tools = remote_data.get("tools", {})
        with httpx.Client(timeout=15.0) as client:
            for tool_name, tool_info in tools.items():
                for ver_name in tool_info.get("versions", {}):
                    local_recipe = recipes_dir / tool_name / ver_name / "recipe.yaml"
                    if not local_recipe.exists():
                        # Fetch recipe.yaml from remote
                        base = f"https://raw.githubusercontent.com/{upstream_repo}/main/recipes/{tool_name}/{ver_name}"
                        r_url = f"{base}/recipe.yaml"
                        r_resp = client.get(r_url)
                        if r_resp.status_code == 200:
                            local_recipe.parent.mkdir(parents=True, exist_ok=True)
                            local_recipe.write_text(r_resp.text)
                            added_count += 1
                            # Lockfiles are optional; fetch them when the hub has them
                            for lock_name in ("pixi.lock", "requirements.lock"):
                                l_resp = client.get(f"{base}/{lock_name}")
                                if l_resp.status_code == 200:
                                    (local_recipe.parent / lock_name).write_text(l_resp.text)

    # Reindex local catalog
    recipes = scan_recipes(recipes_dir)
    export_catalog(recipes_dir, catalog_path)

    console.print(f"[bold green]✔ Synchronization complete! {added_count} new recipe(s) synced.[/bold green]")
    n_tools = len({r.name for r in recipes})
    console.print(f"   [dim]Total tools available locally: {n_tools} ({len(recipes)} versions indexed)[/dim]")

    return added_count, len(recipes)
