"""
Test runner: validates container execution against declared test commands across Docker, Podman, Apptainer, and Singularity.
"""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Union
from rich.console import Console
from rich.panel import Panel

from capsid.models import Recipe, StackRecipe

console = Console()


class TestResult:
    def __init__(
        self,
        passed: bool,
        exit_code: int,
        output: str,
        duration: float,
        engine: str,
        error: Optional[str] = None
    ):
        self.passed = passed
        self.exit_code = exit_code
        self.output = output
        self.duration = duration
        self.engine = engine
        self.error = error


def detect_test_engine(preferred: Optional[str] = None, sif_path: Optional[Path] = None) -> str:
    """Detect available container runtime for testing."""
    if preferred:
        return preferred.lower()

    if sif_path and sif_path.exists():
        if shutil.which("apptainer"):
            return "apptainer"
        if shutil.which("singularity"):
            return "singularity"

    for eng in ["docker", "podman", "apptainer", "singularity"]:
        if shutil.which(eng):
            return eng

    raise RuntimeError("No container test runtime found (checked: docker, podman, apptainer, singularity).")


def run_test(
    recipe: Union[Recipe, StackRecipe],
    image_tag: Optional[str] = None,
    engine: Optional[str] = None,
    sif_path: Optional[Path] = None
) -> TestResult:
    """Run declared test command inside the container using Docker, Podman, Apptainer, or Singularity."""
    selected_engine = detect_test_engine(preferred=engine, sif_path=sif_path)
    tag = image_tag or recipe.canonical_tag

    # Determine command based on engine
    if selected_engine in ["apptainer", "singularity"]:
        target_sif = sif_path or Path(f"{recipe.name}-{recipe.version}.sif")
        if not target_sif.exists():
            # Check if builds/ directory has it
            alt_sif = Path(f"builds/{recipe.name}-{recipe.version}.sif")
            if alt_sif.exists():
                target_sif = alt_sif
            else:
                console.print(f"[yellow]SIF image '{target_sif}' not found. Falling back to docker engine.[/yellow]")
                selected_engine = "docker"

    console.print(f"[bold cyan]🧪 Testing container [green]{tag}[/green] via [magenta]{selected_engine}[/magenta]...[/bold cyan]")
    console.print(f"   [dim]Command: {recipe.test.command}[/dim]")

    start = time.time()

    if selected_engine == "docker":
        cmd = ["docker", "run", "--rm", tag, "bash", "-c", recipe.test.command]
    elif selected_engine == "podman":
        cmd = ["podman", "run", "--rm", tag, "bash", "-c", recipe.test.command]
    elif selected_engine in ["apptainer", "singularity"]:
        target_sif = sif_path or Path(f"{recipe.name}-{recipe.version}.sif")
        if not target_sif.exists() and Path(f"builds/{recipe.name}-{recipe.version}.sif").exists():
            target_sif = Path(f"builds/{recipe.name}-{recipe.version}.sif")
        cmd = [selected_engine, "exec", str(target_sif), "bash", "-c", recipe.test.command]
    else:
        raise ValueError(f"Unsupported test engine '{selected_engine}'")

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        duration = time.time() - start
        output = proc.stdout.strip()

        passed = (proc.returncode == recipe.test.expected_exit_code)
        error_msg = None

        if not passed:
            error_msg = f"Expected exit code {recipe.test.expected_exit_code}, got {proc.returncode}"

        if passed and recipe.test.expected_output:
            if recipe.test.expected_output not in output:
                passed = False
                error_msg = f"Expected substring '{recipe.test.expected_output}' not found in output"

        if passed:
            console.print(f"[bold green]✔ Test PASSED on {selected_engine} in {duration:.2f}s[/bold green]")
            if output:
                console.print(f"   [dim]Output sample: {output[:100]}...[/dim]")
        else:
            console.print(f"[bold red]❌ Test FAILED on {selected_engine}! {error_msg}[/bold red]")
            console.print(Panel(output or "(No output)", title=f"{selected_engine.capitalize()} Output", style="red"))

        return TestResult(
            passed=passed,
            exit_code=proc.returncode,
            output=output,
            duration=duration,
            engine=selected_engine,
            error=error_msg
        )

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        console.print(f"[bold red]❌ Test TIMED OUT after 60s on {selected_engine}![/bold red]")
        return TestResult(passed=False, exit_code=-1, output="", duration=duration, engine=selected_engine, error="Timeout")
    except Exception as e:
        duration = time.time() - start
        console.print(f"[bold red]❌ Error executing test on {selected_engine}: {e}[/bold red]")
        return TestResult(passed=False, exit_code=-1, output="", duration=duration, engine=selected_engine, error=str(e))
