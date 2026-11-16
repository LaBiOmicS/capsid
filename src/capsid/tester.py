"""
Test runner: runs a recipe's test inside the built image with Docker, Podman, Apptainer or Singularity.
"""

import hashlib
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from capsid.models import Recipe, StackRecipe, TestConfig

console = Console()


class TestResult:
    def __init__(
        self, passed: bool, exit_code: int, output: str, duration: float, engine: str, error: str | None = None
    ):
        self.passed = passed
        self.exit_code = exit_code
        self.output = output
        self.duration = duration
        self.engine = engine
        self.error = error


def detect_test_engine(preferred: str | None = None, sif_path: Path | None = None) -> str:
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


TEST_WORKDIR = "/test"


def _prepare_workdir(test: TestConfig) -> Path | None:
    """Write test.files into a fresh host directory mounted at /test (writable by uid 1000)."""
    if not test.uses_workdir:
        return None
    workdir = Path(tempfile.mkdtemp(prefix="capsid-test-"))
    workdir.chmod(0o777)
    for rel, content in test.files.items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    for d in [workdir, *[p for p in workdir.rglob("*") if p.is_dir()]]:
        d.chmod(0o777)
    return workdir


def _test_env(test: TestConfig) -> list[str]:
    if test.random_seed is None:
        return []
    return [f"CAPSID_RANDOM_SEED={test.random_seed}", f"PYTHONHASHSEED={test.random_seed}"]


def apptainer_source(image: str) -> str:
    """
    Image URI for Apptainer/Singularity when no SIF file is given: a registry reference is pulled
    (docker://), a local tag such as 'samtools:1.25' is read from the Docker daemon (docker-daemon:).
    """
    first = image.split("/", 1)[0]
    is_registry = "/" in image and ("." in first or ":" in first or first == "localhost")
    return f"docker://{image}" if is_registry else f"docker-daemon:{image}"


def build_test_command(
    engine: str,
    test: TestConfig,
    image: str,
    sif: Path | None,
    workdir: Path | None,
) -> list[str]:
    """Container invocation that runs test.command (optionally inside /test with seeded env)."""
    env = _test_env(test)
    if engine in ("docker", "podman"):
        cmd = [engine, "run", "--rm"]
        for e in env:
            cmd += ["-e", e]
        if workdir:
            cmd += ["-v", f"{workdir}:{TEST_WORKDIR}", "-w", TEST_WORKDIR]
        return [*cmd, image, "bash", "-c", test.command]
    if engine in ("apptainer", "singularity"):
        cmd = [engine, "exec"]
        for e in env:
            cmd += ["--env", e]
        if workdir:
            cmd += ["--bind", f"{workdir}:{TEST_WORKDIR}", "--pwd", TEST_WORKDIR]
        return [*cmd, str(sif) if sif else apptainer_source(image), "bash", "-c", test.command]
    raise ValueError(f"Unsupported test engine '{engine}'")


def check_result(test: TestConfig, exit_code: int, output: str, workdir: Path | None) -> str | None:
    """None when every expectation holds, otherwise the first failure."""
    if exit_code != test.expected_exit_code:
        return f"Expected exit code {test.expected_exit_code}, got {exit_code}"
    if test.expected_output and test.expected_output not in output:
        return f"Expected substring '{test.expected_output}' not found in output"
    if test.output_file:
        produced = (workdir / test.output_file) if workdir else None
        if not produced or not produced.is_file():
            return f"Expected output file '{test.output_file}' was not created"
        if test.expected_checksum:
            digest = hashlib.sha256(produced.read_bytes()).hexdigest()
            if digest != test.expected_checksum:
                return f"Checksum of '{test.output_file}' is {digest}, expected {test.expected_checksum}"
    return None


def _find_sif(recipe: Recipe | StackRecipe, sif_path: Path | None) -> Path | None:
    candidates = (
        [sif_path]
        if sif_path
        else [
            Path(f"{recipe.name}-{recipe.version}.sif"),
            Path(f"builds/{recipe.name}-{recipe.version}.sif"),
        ]
    )
    return next((c for c in candidates if c and c.exists()), None)


def run_test(
    recipe: Recipe | StackRecipe,
    image_tag: str | None = None,
    engine: str | None = None,
    sif_path: Path | None = None,
) -> TestResult:
    """Run the declared test inside the container using Docker, Podman, Apptainer, or Singularity."""
    test = recipe.test
    selected_engine = detect_test_engine(preferred=engine, sif_path=sif_path)
    tag = image_tag or recipe.canonical_tag

    sif = None
    if selected_engine in ("apptainer", "singularity"):
        sif = _find_sif(recipe, sif_path)
        if sif_path and not sif:
            console.print(f"[bold red]SIF file '{sif_path}' not found.[/bold red]")
            return TestResult(
                passed=False, exit_code=-1, output="", duration=0.0, engine=selected_engine, error="SIF not found"
            )
        if not sif:
            console.print(f"   [dim]No SIF file; using {apptainer_source(tag)}[/dim]")

    console.print(
        f"[bold cyan]🧪 Testing container [green]{tag}[/green] via [magenta]{selected_engine}[/magenta]...[/bold cyan]"
    )
    console.print(f"   [dim]Command: {test.command}[/dim]")

    workdir = _prepare_workdir(test)
    start = time.time()
    try:
        cmd = build_test_command(selected_engine, test, tag, sif, workdir)
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=test.timeout, check=False
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - start
            console.print(f"[bold red]❌ Test TIMED OUT after {test.timeout}s on {selected_engine}![/bold red]")
            return TestResult(
                passed=False, exit_code=-1, output="", duration=duration, engine=selected_engine, error="Timeout"
            )
        except OSError as e:
            duration = time.time() - start
            console.print(f"[bold red]❌ Error executing test on {selected_engine}: {e}[/bold red]")
            return TestResult(
                passed=False, exit_code=-1, output="", duration=duration, engine=selected_engine, error=str(e)
            )
        duration = time.time() - start
        output = proc.stdout.strip()
        error_msg = check_result(test, proc.returncode, output, workdir)
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    passed = error_msg is None
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
        error=error_msg,
    )
