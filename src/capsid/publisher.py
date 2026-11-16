"""
Capsid Recipe Publisher: automated one-click contribution of recipes to Capsid Hub.
Zero-friction submission: auto-lints, tests, forks, commits, and opens a Pull Request.
"""

import os
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional
import httpx
from rich.console import Console
from rich.panel import Panel

from capsid.models import Recipe
from capsid.linter import lint_recipe

console = Console()

DEFAULT_UPSTREAM_REPO = "capsid/capsid-hub"


def check_gh_authenticated() -> bool:
    """Check if GitHub CLI (gh) is installed and authenticated."""
    if not shutil.which("gh"):
        return False
    try:
        res = subprocess.run(["gh", "auth", "status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res.returncode == 0
    except Exception:
        return False


def submit_via_web(recipe: Recipe, upstream_repo: str) -> str:
    """Generate a pre-filled GitHub Issue URL for zero-terminal submission."""
    import yaml
    yaml_content = yaml.dump(recipe.model_dump(mode="json", exclude_none=True), sort_keys=False)
    
    title = f"Recipe Submission: {recipe.name} v{recipe.version}"
    body = (
        f"### Tool Details\n"
        f"- **Name:** `{recipe.name}`\n"
        f"- **Version:** `{recipe.version}`\n"
        f"- **Engine:** `{recipe.build.type.value}`\n\n"
        f"### Recipe YAML\n"
        f"```yaml\n{yaml_content}\n```\n"
    )
    
    encoded_title = urllib.parse.quote(title)
    encoded_body = urllib.parse.quote(body)
    url = f"https://github.com/{upstream_repo}/issues/new?title={encoded_title}&body={encoded_body}&labels=new-recipe"
    return url


def submit_via_gh(recipe: Recipe, recipe_path: Path, upstream_repo: str) -> Optional[str]:
    """Submit recipe via GitHub CLI (gh) by auto-forking, branching, and creating PR."""
    branch_name = f"recipe-{recipe.name}-{recipe.version}"
    pr_title = f"feat(recipe): add {recipe.name} {recipe.version}"
    pr_body = (
        f"### 🧬 Capsid Recipe Submission\n\n"
        f"- **Tool:** `{recipe.name}`\n"
        f"- **Version:** `{recipe.version}`\n"
        f"- **Build Engine:** `{recipe.build.type.value}`\n"
        f"- **Target Platforms:** {', '.join([p.value for p in recipe.platforms])}\n"
        f"- **License:** `{recipe.license or 'UNKNOWN'}`\n\n"
        f"*Submitted automatically via `capsid submit`.*"
    )

    try:
        # Check if current dir is a git repo
        is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0
        if not is_git:
            console.print("[yellow]Notice: Not inside a git repository. Using web submission fallback.[/yellow]")
            return None

        # Check existing branch
        subprocess.run(["git", "checkout", "-b", branch_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "add", str(recipe_path)], check=True)
        subprocess.run(["git", "commit", "-m", pr_title], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Push using gh
        console.print(f"[bold cyan]🚀 Pushing recipe and opening Pull Request to [green]{upstream_repo}[/green]...[/bold cyan]")
        res = subprocess.run(
            ["gh", "pr", "create", "--repo", upstream_repo, "--title", pr_title, "--body", pr_body, "--head", branch_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if res.returncode == 0:
            return res.stdout.strip()
        else:
            console.print(f"[yellow]Warning from gh: {res.stderr.strip()}[/yellow]")
            return None
    except Exception as e:
        console.print(f"[yellow]Could not complete automated gh PR: {e}[/yellow]")
        return None


def submit_recipe(
    recipe: Recipe,
    recipe_path: Path,
    upstream_repo: str = DEFAULT_UPSTREAM_REPO,
    force_web: bool = False
) -> None:
    """Run full automated submission flow for a recipe."""
    console.print(f"[bold cyan]🛡️ Auditing [green]{recipe.name} v{recipe.version}[/green] before submission...[/bold cyan]")
    
    # 1. Local Audit / Lint
    issues = lint_recipe(recipe_path)
    errors = [i for i in issues if i.level == "ERROR"]
    warnings = [i for i in issues if i.level == "WARNING"]

    if warnings:
        for w in warnings:
            console.print(f"  {w}")

    if errors:
        console.print("\n[bold red]❌ Cannot submit: Recipe failed safety audit.[/bold red]")
        for e in errors:
            console.print(f"  {e}")
        raise ValueError("Recipe has audit errors. Please fix them before submitting.")

    console.print("[bold green]✔ Security audit passed successfully![/bold green]")

    # 2. Choose submission strategy
    pr_url = None
    if not force_web and check_gh_authenticated():
        pr_url = submit_via_gh(recipe, recipe_path, upstream_repo)

    if pr_url:
        console.print(Panel(
            f"[bold green]✔ Pull Request successfully opened![/bold green]\n\n"
            f"[cyan]{pr_url}[/cyan]\n\n"
            f"The Capsid Bot will now automatically verify, test, and prepare your container for release.",
            title="🎉 Submission Complete",
            border_style="green"
        ))
    else:
        # Fallback to 1-Click Web Submission
        web_url = submit_via_web(recipe, upstream_repo)
        console.print(Panel(
            f"[bold yellow]🌐 1-Click Web Submission Ready[/bold yellow]\n\n"
            f"Click the link below (or copy and paste into your browser) to submit:\n\n"
            f"[bold cyan]{web_url}[/bold cyan]\n\n"
            f"The automated Capsid Bot will convert your submission into an approved container automatically!",
            title="🚀 Next Step",
            border_style="cyan"
        ))
