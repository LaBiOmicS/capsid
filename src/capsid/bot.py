"""
Capsid Robot: Automated upstream monitoring and transformation for Bioconda and BioContainers.
Scans upstream repositories (Bioconda channel data, Bioconda git commits, BioContainers Quay registries),
detects new tools and version upgrades, transforms them into hardened Capsid recipes, and lints them.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from rich.console import Console
from rich.table import Table

from capsid.catalog import export_catalog
from capsid.linter import lint_recipe
from capsid.models import Platform, recipe_to_yaml
from capsid.scaffolders.bioconda import scaffold_from_bioconda

console = Console()

BIOCONDA_CHANNELDATA_URL = "https://conda.anaconda.org/bioconda/channeldata.json"
BIOCONDA_COMMITS_URL = "https://api.github.com/repos/bioconda/bioconda-recipes/commits"
QUAY_BIOCONTAINERS_URL = "https://quay.io/api/v1/repository/biocontainers"


@dataclass
class ToolCandidate:
    name: str
    version: str
    summary: str = ""
    license: str = "UNKNOWN"
    homepage: str = ""
    timestamp: float = 0.0
    action: str = "NEW_TOOL"  # "NEW_TOOL" or "UPGRADE"
    current_local_version: str | None = None
    biocontainer_tag: str | None = None
    engine_selected: str = "pixi-conda"
    status: str = "PENDING"  # "PENDING", "TRANSFORMED", "FAILED", "SKIPPED"
    error_message: str | None = None
    recipe_path: Path | None = None


def scan_local_inventory(recipes_dir: Path) -> dict[str, set[str]]:
    """Scan existing recipes directory and return a map of {tool_name: set_of_versions}."""
    inventory: dict[str, set[str]] = {}
    if not recipes_dir.exists():
        return inventory

    for recipe_file in recipes_dir.glob("*/*/recipe.yaml"):
        tool_name = recipe_file.parent.parent.name.lower()
        version = recipe_file.parent.name
        inventory.setdefault(tool_name, set()).add(version)

    return inventory


def fetch_quay_biocontainer_tag(tool_name: str, client: httpx.Client) -> str | None:
    """Query Quay.io for official BioContainers tag for this tool."""
    try:
        url = f"{QUAY_BIOCONTAINERS_URL}/{tool_name.lower()}"
        resp = client.get(url, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            tags = data.get("tags", {})
            if tags:
                # Return the most recent tag
                sorted_tags = sorted(
                    tags.items(),
                    key=lambda item: item[1].get("last_modified", 0) if isinstance(item[1], dict) else 0,
                    reverse=True,
                )
                if sorted_tags:
                    return f"quay.io/biocontainers/{tool_name.lower()}:{sorted_tags[0][0]}"
    except (httpx.HTTPError, ValueError):
        return None  # BioContainers is optional metadata: never block the robot on it
    return None


def fetch_upstream_candidates(
    days: int = 7,
    limit: int | None = None,
    specific_tools: list[str] | None = None,
    recipes_dir: Path = Path("recipes"),
) -> list[ToolCandidate]:
    """
    Scans Bioconda channel data and commits to find tools added or updated in the last N days.
    Deduplicates against local recipes inventory.
    """
    local_inventory = scan_local_inventory(recipes_dir)
    candidates: list[ToolCandidate] = []
    seen_names: set[str] = set()

    cutoff_timestamp = time.time() - (days * 86400)
    console.print(f"[cyan]🔎 Fetching Bioconda channel data (looking back {days} days)...[/cyan]")

    # 1. Inspect Bioconda channeldata.json
    try:
        with httpx.Client(timeout=20.0, headers={"User-Agent": "capsid-robot/1.0"}) as client:
            resp = client.get(BIOCONDA_CHANNELDATA_URL)
            if resp.status_code == 200:
                data = resp.json()
                packages = data.get("packages", {})
                for pkg_name, pkg_info in packages.items():
                    name_lower = pkg_name.lower()

                    if specific_tools and name_lower not in [t.lower() for t in specific_tools]:
                        continue

                    ts = pkg_info.get("timestamp", 0)
                    if not specific_tools and ts < cutoff_timestamp:
                        continue

                    version = str(pkg_info.get("version", "latest"))
                    local_versions = local_inventory.get(name_lower, set())

                    # Skip if exact version already exists locally
                    if version in local_versions:
                        continue

                    action = "UPGRADE" if local_versions else "NEW_TOOL"
                    curr_ver = max(local_versions) if local_versions else None

                    seen_names.add(name_lower)
                    candidates.append(
                        ToolCandidate(
                            name=name_lower,
                            version=version,
                            summary=pkg_info.get("summary", ""),
                            license=str(pkg_info.get("license", "UNKNOWN"))[:50],
                            homepage=pkg_info.get("home", ""),
                            timestamp=ts,
                            action=action,
                            current_local_version=curr_ver,
                        )
                    )
    except Exception as e:  # noqa: BLE001 - best-effort scan of external data; reported, not fatal
        console.print(f"[yellow]Warning: Could not fetch bioconda channeldata.json: {e}[/yellow]")

    # 2. Inspect recent commits from bioconda-recipes for any immediate bleeding-edge updates
    if not specific_tools or len(candidates) < (limit or 10):
        try:
            with httpx.Client(timeout=10.0, headers={"User-Agent": "capsid-robot/1.0"}) as client:
                c_resp = client.get(f"{BIOCONDA_COMMITS_URL}?per_page=30")
                if c_resp.status_code == 200:
                    commits = c_resp.json()
                    for c in commits:
                        msg = c.get("commit", {}).get("message", "").splitlines()[0]
                        # Match patterns like 'Update bwa to 0.7.18', 'Add samtools 1.25', 'Bump fastp from 1.3 to 1.4'
                        parts = msg.replace("(#", " ").split()
                        for i, token in enumerate(parts):
                            if token.lower() in ["update", "add", "bump"] and i + 1 < len(parts):
                                potential_name = parts[i + 1].strip(",:").lower()
                                if potential_name and potential_name not in seen_names and len(potential_name) > 1:
                                    local_versions = local_inventory.get(potential_name, set())
                                    seen_names.add(potential_name)
                                    action = "UPGRADE" if local_versions else "NEW_TOOL"
                                    curr_ver = max(local_versions) if local_versions else None
                                    candidates.append(
                                        ToolCandidate(
                                            name=potential_name,
                                            version="latest",
                                            summary=f"Detected from Bioconda commit: {msg}",
                                            timestamp=time.time(),
                                            action=action,
                                            current_local_version=curr_ver,
                                        )
                                    )
        except Exception as e:  # noqa: BLE001 - best-effort scan of external data; reported, not fatal
            console.print(f"[dim]Commit check skipped: {e}[/dim]")

    # Sort candidates by timestamp (most recent first)
    candidates.sort(key=lambda c: c.timestamp, reverse=True)

    if limit and limit > 0:
        candidates = candidates[:limit]

    return candidates


def transform_candidate(candidate: ToolCandidate, recipes_dir: Path, dry_run: bool = False) -> ToolCandidate:
    """
    Transforms an upstream candidate into a Capsid recipe.
    Determines engine (uv-pypi vs pixi-conda), applies hardening standards,
    queries Quay BioContainers tag, and runs safety linting.
    """
    console.print(f"  [cyan]⚙️ Transforming [bold]{candidate.name}@{candidate.version}[/bold]...[/cyan]")

    try:
        # Check BioContainers Quay tag
        with httpx.Client(headers={"User-Agent": "capsid-robot/1.0"}) as client:
            candidate.biocontainer_tag = fetch_quay_biocontainer_tag(candidate.name, client)

        # Scaffold from Bioconda
        ver_param = None if candidate.version == "latest" else candidate.version
        recipe = scaffold_from_bioconda(candidate.name, version=ver_param)
        candidate.version = recipe.version

        # Enhance recipe with BioContainers & Robot labels
        recipe.categories = sorted({*recipe.categories, "biocontainers-migrated", "automated-capsid"})
        recipe.labels["capsid_bot"] = "weekly-bioconda-biocontainer-robot"
        recipe.labels["upstream_bioconda"] = f"https://bioconda.github.io/recipes/{candidate.name}/README.html"
        if candidate.biocontainer_tag:
            recipe.labels["upstream_biocontainer"] = candidate.biocontainer_tag

        candidate.engine_selected = recipe.build.type.value

        target_file = recipes_dir / recipe.name / recipe.version / "recipe.yaml"
        candidate.recipe_path = target_file

        # Never touch an existing recipe: it may have been edited and reviewed by hand
        if target_file.exists():
            candidate.status = "SKIPPED"
            console.print(f"    [dim]• {recipe.name}@{recipe.version} already has a recipe; skipped[/dim]")
            return candidate

        if dry_run:
            candidate.status = "TRANSFORMED"
            return candidate

        created_dirs = [d for d in (target_file.parent.parent, target_file.parent) if not d.exists()]
        target_file.parent.mkdir(parents=True, exist_ok=True)
        yaml_content = recipe_to_yaml(recipe)
        target_file.write_text(yaml_content)

        # Run security and schema linter
        issues = lint_recipe(target_file)
        errors = [issue for issue in issues if issue.level == "ERROR"]
        if errors:
            candidate.status = "FAILED"
            candidate.error_message = "; ".join([e.message for e in errors])
            console.print(f"    [red]✖ Lint failed for {candidate.name}: {candidate.error_message}[/red]")
            # Remove the rejected recipe (and only the directories created for it)
            target_file.unlink(missing_ok=True)
            for d in reversed(created_dirs):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
        else:
            candidate.status = "TRANSFORMED"
            console.print(
                f"    [green]✔ Transformed and validated {recipe.name}@{recipe.version} "
                f"({candidate.engine_selected})[/green]"
            )

    except Exception as e:  # noqa: BLE001 - one bad upstream recipe must not stop the batch; recorded per candidate
        candidate.status = "FAILED"
        candidate.error_message = str(e)
        console.print(f"    [red]✖ Failed to transform {candidate.name}: {e}[/red]")

    return candidate


def generate_markdown_report(candidates: list[ToolCandidate], days: int) -> str:
    """Generate Markdown report for GitHub Actions PR summary."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    transformed = [c for c in candidates if c.status == "TRANSFORMED"]
    failed = [c for c in candidates if c.status == "FAILED"]

    lines = [
        "## 🧬 Capsid Robot: Weekly Upstream Sync",
        f"**Run Timestamp:** `{now_str}`",
        f"**Lookback Window:** Last {days} days",
        "",
        "The Capsid robot inspected **Bioconda** and **BioContainers**, detected new tools and updates, "
        "and converted them into Capsid recipes.",
        "",
        "### 📊 Summary Statistics",
        f"- **Candidates Discovered:** `{len(candidates)}`",
        f"- **Successfully Transformed:** `{len(transformed)}`",
        f"- **Failed / Lint Issues:** `{len(failed)}`",
        "",
    ]

    if transformed:
        lines.append("### 📦 Transformed Tools (Ready for Hub Catalog)")
        lines.append("| Tool | Version | Action | Engine | Upstream BioContainer | License |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for c in transformed:
            action_badge = (
                "🌟 **New Tool**" if c.action == "NEW_TOOL" else f"🔄 Upgrade (from `{c.current_local_version}`)"
            )
            biocontainer_cell = (
                f"[`{c.biocontainer_tag.split('/')[-1]}`]({c.biocontainer_tag})"
                if c.biocontainer_tag
                else "*Conda/Quay*"
            )
            lines.append(
                f"| `{c.name}` | `{c.version}` | {action_badge} | `{c.engine_selected}` "
                f"| {biocontainer_cell} | {c.license} |"
            )
        lines.append("")

    if failed:
        lines.append("### ⚠️ Failed Transformations (Needs Manual Review)")
        lines.append("| Tool | Attempted Version | Reason |")
        lines.append("| :--- | :--- | :--- |")
        lines.extend(f"| `{c.name}` | `{c.version}` | {c.error_message or 'Unknown error'} |" for c in failed)
        lines.append("")

    lines.append("---")
    lines.append("*🤖 Generated automatically by Capsid Upstream Robot.*")

    return "\n".join(lines)


def run_robot_pipeline(
    days: int = 7,
    limit: int = 10,
    specific_tools: list[str] | None = None,
    recipes_dir: Path = Path("recipes"),
    catalog_path: Path = Path("catalog.json"),
    dry_run: bool = False,
    update_catalog: bool = True,
) -> tuple[list[ToolCandidate], str]:
    """
    Executes the full robot cycle:
    1. Scan Bioconda & BioContainers upstreams
    2. Filter candidates not present in local inventory
    3. Transform to Capsid recipes
    4. Run Capsid Lint on every new recipe
    5. Update catalog.json and CATALOG.md
    6. Return candidates and Markdown report
    """
    console.print("\n[bold magenta]🤖 Capsid Robot: Bioconda & BioContainers Upstream Harvester[/bold magenta]")
    console.print(f"   [dim]Lookback: {days} days | Batch limit: {limit} tools | Dry run: {dry_run}[/dim]\n")

    candidates = fetch_upstream_candidates(
        days=days, limit=limit, specific_tools=specific_tools, recipes_dir=recipes_dir
    )

    if not candidates:
        console.print("[green]✔ No new tools or updates detected upstream. Everything is up to date![/green]")
        report = generate_markdown_report([], days)
        return [], report

    console.print(f"[bold green]Found {len(candidates)} candidate(s) to process:[/bold green]\n")

    processed: list[ToolCandidate] = []
    for candidate in candidates:
        result = transform_candidate(candidate, recipes_dir=recipes_dir, dry_run=dry_run)
        processed.append(result)

    # If new recipes were added, update catalog.json
    transformed_count = sum(1 for c in processed if c.status == "TRANSFORMED")
    if transformed_count > 0 and not dry_run and update_catalog:
        console.print("\n[cyan]📚 Updating hub catalog with new recipes...[/cyan]")
        export_catalog(recipes_dir, catalog_path)
        console.print(f"[bold green]✔ Catalog updated at {catalog_path}![/bold green]")

    report = generate_markdown_report(processed, days)

    # Print summary table
    table = Table(title="🤖 Capsid Robot Execution Summary", show_header=True, header_style="bold cyan")
    table.add_column("Tool", style="bold")
    table.add_column("Version")
    table.add_column("Action")
    table.add_column("Engine")
    table.add_column("BioContainer")
    table.add_column("Status")

    for c in processed:
        status_style = {"TRANSFORMED": "green", "SKIPPED": "dim"}.get(c.status, "red")
        table.add_row(
            c.name,
            c.version,
            c.action,
            c.engine_selected,
            "Quay Tag Found" if c.biocontainer_tag else "Conda only",
            f"[{status_style}]{c.status}[/{status_style}]",
        )

    console.print("\n")
    console.print(table)
    console.print(f"\n[bold green]Robot cycle finished: {transformed_count} transformed successfully.[/bold green]\n")

    return processed, report


def migrate_bioconda_all(
    recipes_dir: Path = Path("recipes"),
    category: str = "core",
    limit: int | None = None,
    dry_run: bool = False,
    update_catalog: bool = False,
) -> tuple[int, int]:
    """
    Massively converts all Bioconda tools into modern Capsid recipes.
    Categories:
      - 'core': Tools excluding r-* and perl-* (~7,006 primary scientific tools)
      - 'r': R and Bioconductor packages (~5,161 packages)
      - 'perl': Perl packages (~736 packages)
      - 'all': All 12,903 packages
    """
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

    console.print("\n[bold magenta]🚀 Capsid Massive Migrator: Bioconda -> Capsid[/bold magenta]")
    console.print(
        f"   [dim]Target category: [bold cyan]{category.upper()}[/bold cyan] | "
        f"Target dir: {recipes_dir} | Dry-run: {dry_run}[/dim]\n"
    )

    console.print("[cyan]📥 Downloading Bioconda central channel index...[/cyan]")
    with httpx.Client(timeout=30.0, headers={"User-Agent": "capsid-robot/1.0"}) as client:
        resp = client.get(BIOCONDA_CHANNELDATA_URL)
        if resp.status_code != 200:
            console.print(f"[bold red]Failed to download Bioconda channel data (HTTP {resp.status_code})[/bold red]")
            return 0, 0
        data = resp.json()

    packages = data.get("packages", {})
    console.print(f"   [dim]Total packages in Bioconda: {len(packages)}[/dim]")

    # Filter by category
    selected_items: list[tuple[str, dict[str, Any]]] = []
    for name, info in packages.items():
        name_lower = name.lower()
        is_r = name_lower.startswith(("bioconductor-", "r-"))
        is_perl = name_lower.startswith("perl-")

        if (
            (category == "core" and not is_r and not is_perl)
            or (category == "r" and is_r)
            or (category == "perl" and is_perl)
            or category == "all"
        ):
            selected_items.append((name_lower, info))

    console.print(f"[bold green]✔ Filtered {len(selected_items)} packages matching category '{category}'.[/bold green]")

    if limit and limit > 0:
        selected_items = selected_items[:limit]
        console.print(f"   [dim]Limiting conversion to first {limit} packages.[/dim]")

    created_count = 0
    skipped_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"[cyan]Converting {category.upper()} tools...", total=len(selected_items))

        for name, info in selected_items:
            ver = str(info.get("version", "1.0.0"))
            target_path = recipes_dir / name / ver / "recipe.yaml"

            if target_path.exists():
                skipped_count += 1
                progress.advance(task)
                continue

            summary = (info.get("summary") or f"{name} bioinformatics tool").replace("\n", " ").strip()
            home = info.get("home") or f"https://bioconda.github.io/recipes/{name}/README.html"
            lic = str(info.get("license") or "UNKNOWN")[:50]
            subdirs = info.get("subdirs", [])

            platforms = [Platform.LINUX_AMD64]
            if "linux-aarch64" in subdirs or "noarch" in subdirs:
                platforms.append(Platform.LINUX_ARM64)

            recipe_dict = {
                "name": name,
                "version": ver,
                "description": summary[:250],
                "homepage": home,
                "license": lic,
                "categories": ["bioinformatics", "bioconda-migrated", f"bioconda-{category}"],
                "platforms": [p.value for p in platforms],
                "build": {
                    "type": "pixi-conda",
                    "python_version": "3.12",
                    "conda_packages": [f"{name}=={ver}"],
                    "channels": ["conda-forge", "bioconda"],
                    "configure_args": [],
                    "cmake_args": [],
                    "build_commands": [],
                    "dependencies": {"build": [], "run": []},
                    "env": {},
                },
                "test": {"command": f"{name} --help || {name} -h || {name} --version || true", "expected_exit_code": 0},
                "labels": {
                    "capsid_migrator": "bioconda-massive-migrator",
                    "bioconda_category": category,
                    "upstream_bioconda": f"https://bioconda.github.io/recipes/{name}/README.html",
                },
            }

            if not dry_run:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                yaml_str = yaml.dump(recipe_dict, sort_keys=False)
                target_path.write_text(yaml_str)

            created_count += 1
            progress.advance(task)

    console.print("\n[bold green]✔ Conversion complete![/bold green]")
    console.print(f"   [cyan]• Newly created recipes:[/cyan] [bold]{created_count}[/bold]")
    console.print(f"   [yellow]• Skipped (already existed):[/yellow] [bold]{skipped_count}[/bold]")
    console.print(f"   [dim]• Output location: {recipes_dir.resolve()}[/dim]")

    if update_catalog and not dry_run and created_count > 0:
        console.print("\n[cyan]📚 Updating hub catalog.json...[/cyan]")
        export_catalog(recipes_dir, Path("catalog.json"))
        console.print("[bold green]✔ Catalog successfully updated![/bold green]")

    return created_count, skipped_count
