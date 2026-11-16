"""
Capsid Recipe Publisher: automated one-click contribution of recipes to Capsid Hub.
Zero-friction submission: auto-lints, tests, forks, commits, and opens a Pull Request.
"""

import shutil
import subprocess
import urllib.parse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from capsid.linter import lint_recipe
from capsid.locking import lock_paths
from capsid.models import Recipe, recipe_to_yaml
from capsid.project import HUB_REPO

console = Console()

DEFAULT_UPSTREAM_REPO = HUB_REPO


def check_gh_authenticated() -> bool:
    """Check if GitHub CLI (gh) is installed and authenticated."""
    if not shutil.which("gh"):
        return False
    try:
        return subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, check=False).returncode == 0
    except OSError:
        return False


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def submit_via_web(recipe: Recipe, upstream_repo: str) -> str:
    """Generate a pre-filled GitHub Issue URL for zero-terminal submission."""

    yaml_content = recipe_to_yaml(recipe)

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


def submit_via_gh(recipe: Recipe, recipe_path: Path, upstream_repo: str) -> str | None:
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

    if _run("git", "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]Notice: Not inside a git repository. Using web submission fallback.[/yellow]")
        return None

    def fail(step: str, res: subprocess.CompletedProcess) -> None:
        console.print(f"[yellow]Could not {step}: {(res.stderr or res.stdout).strip()}[/yellow]")

    # The pull request comes from the user's fork (created if missing; a no-op if it exists)
    login_res = _run("gh", "api", "user", "--jq", ".login")
    if login_res.returncode != 0:
        fail("determine the GitHub login", login_res)
        return None
    login = login_res.stdout.strip()
    fork_res = _run("gh", "repo", "fork", upstream_repo, "--clone=false")
    if fork_res.returncode != 0:
        fail(f"fork {upstream_repo}", fork_res)
        return None
    fork_url = f"https://github.com/{login}/{upstream_repo.split('/', 1)[1]}.git"

    files = [recipe_path, *(p for p in lock_paths(recipe_path) if p.is_file())]
    for step, cmd in (
        (f"create branch {branch_name}", ("git", "switch", "-c", branch_name)),
        ("stage the recipe", ("git", "add", "--", *map(str, files))),
        ("commit the recipe", ("git", "commit", "-m", pr_title, "--", *map(str, files))),
        (f"push to {fork_url}", ("git", "push", fork_url, f"HEAD:refs/heads/{branch_name}")),
    ):
        res = _run(*cmd)
        if res.returncode != 0:
            fail(step, res)
            return None

    console.print(f"[bold cyan]🚀 Opening Pull Request to [green]{upstream_repo}[/green]...[/bold cyan]")
    res = _run(
        "gh",
        "pr",
        "create",
        "--repo",
        upstream_repo,
        "--title",
        pr_title,
        "--body",
        pr_body,
        "--head",
        f"{login}:{branch_name}",
    )
    if res.returncode != 0:
        fail("open the pull request", res)
        return None
    console.print(f"[dim]Your working copy is now on branch '{branch_name}'.[/dim]")
    return res.stdout.strip()


def submit_recipe(
    recipe: Recipe, recipe_path: Path, upstream_repo: str = DEFAULT_UPSTREAM_REPO, force_web: bool = False
) -> None:
    """Run full automated submission flow for a recipe."""
    console.print(
        f"[bold cyan]🛡️ Auditing [green]{recipe.name} v{recipe.version}[/green] before submission...[/bold cyan]"
    )

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
        console.print(
            Panel(
                f"[bold green]✔ Pull Request successfully opened![/bold green]\n\n"
                f"[cyan]{pr_url}[/cyan]\n\n"
                f"The Capsid Bot will now automatically verify, test, and prepare your container for release.",
                title="🎉 Submission Complete",
                border_style="green",
            )
        )
    else:
        # Fallback to 1-Click Web Submission
        web_url = submit_via_web(recipe, upstream_repo)
        console.print(
            Panel(
                f"[bold yellow]🌐 1-Click Web Submission Ready[/bold yellow]\n\n"
                f"Click the link below (or copy and paste into your browser) to submit:\n\n"
                f"[bold cyan]{web_url}[/bold cyan]\n\n"
                f"The automated Capsid Bot will convert your submission into an approved container automatically!",
                title="🚀 Next Step",
                border_style="cyan",
            )
        )
