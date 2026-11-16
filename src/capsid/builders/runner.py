"""
Build runner: executes multi-engine OCI (Docker, Podman) and SIF (Apptainer, Singularity) builds with live logging.
Optimized for HPC clusters and cloud CI/CD environments.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Sequence, List
from rich.console import Console

from capsid.models import Recipe
from capsid.builders.dockerfile_gen import generate_dockerfile

console = Console()


class BuildResult:
    def __init__(
        self,
        tag: str,
        duration: float,
        size_mb: Optional[float] = None,
        sif_path: Optional[Path] = None,
        oci_engine: str = "docker",
        sif_engine: Optional[str] = None,
        dockerfile: Optional[Path] = None,
    ):
        self.tag = tag
        self.duration = duration
        self.size_mb = size_mb
        self.sif_path = sif_path
        self.oci_engine = oci_engine
        self.sif_engine = sif_engine
        self.dockerfile = dockerfile


def detect_oci_engine() -> str:
    """Detect available OCI container builder (docker, podman, nerdctl)."""
    for engine in ["docker", "podman", "nerdctl"]:
        if shutil.which(engine):
            return engine
    raise RuntimeError(
        "No OCI container engine found (checked: docker, podman, nerdctl). "
        "Please install Docker or Podman."
    )


def detect_sif_engine() -> Optional[str]:
    """Detect available SIF builder (apptainer, singularity)."""
    for engine in ["apptainer", "singularity"]:
        if shutil.which(engine):
            return engine
    return None


def get_image_size_mb(tag: str, engine: str = "docker") -> Optional[float]:
    """Inspect container image size in Megabytes."""
    try:
        res = subprocess.run(
            [engine, "image", "inspect", tag, "--format={{.Size}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        bytes_val = int(res.stdout.strip())
        return round(bytes_val / (1024 * 1024), 2)
    except Exception:
        return None


def build_recipe(
    recipe: Recipe,
    tag: Optional[str] = None,
    build_apptainer: bool = False,
    output_sif: Optional[Path] = None,
    no_cache: bool = False,
    build_dir: Optional[Path] = None,
    oci_engine: Optional[str] = None,
) -> BuildResult:
    """
    Build OCI container using Docker BuildKit / Podman, and optionally export to Apptainer/Singularity SIF.
    """
    selected_oci = oci_engine or detect_oci_engine()
    image_tag = tag or recipe.canonical_tag
    start_time = time.time()

    # Create isolated build directory
    work_dir = build_dir or Path(f".capsid-build/{recipe.name}")
    work_dir.mkdir(parents=True, exist_ok=True)

    dockerfile_path = work_dir / "Dockerfile"
    dockerfile_content = generate_dockerfile(recipe)
    dockerfile_path.write_text(dockerfile_content)

    console.print(f"[bold cyan]🔨 Building container for [green]{recipe.name}[/green] v{recipe.version}...[/bold cyan]")
    console.print(f"   [dim]Engine: {recipe.build.type.value} | OCI Builder: {selected_oci} | Tag: {image_tag}[/dim]")

    # Assemble OCI build command
    cmd = [
        selected_oci, "build",
        "-t", image_tag,
        "-f", str(dockerfile_path),
        # BuildKit only honours SOURCE_DATE_EPOCH when passed as a build-arg
        "--build-arg", f"SOURCE_DATE_EPOCH={recipe.build.source_date_epoch}",
        str(work_dir)
    ]
    if no_cache:
        cmd.append("--no-cache")

    env = os.environ.copy()
    if selected_oci == "docker":
        env["DOCKER_BUILDKIT"] = "1"

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env
    )

    for line in iter(process.stdout.readline, ''):
        if line:
            stripped = line.strip()
            if any(k in stripped for k in ["Step", "CACHED", "DONE", "ERROR", "naming to", "Writing"]):
                console.print(f"   [dim]{stripped}[/dim]")

    process.stdout.close()
    return_code = process.wait()

    if return_code != 0:
        raise RuntimeError(f"{selected_oci} build failed with exit code {return_code}. Check Dockerfile at {dockerfile_path}")

    duration = time.time() - start_time
    size_mb = get_image_size_mb(image_tag, engine=selected_oci)
    console.print(f"[bold green]✔ OCI image built successfully with {selected_oci} in {duration:.1f}s! Size: {size_mb} MB[/bold green]")

    sif_target = None
    selected_sif = None
    if build_apptainer:
        selected_sif = detect_sif_engine()
        sif_target = output_sif or Path(f"{recipe.name}-{recipe.version}.sif")

        if not selected_sif:
            console.print("[yellow]Notice: Neither 'apptainer' nor 'singularity' found on PATH.[/yellow]")
            console.print("   Exporting OCI tarball archive for remote HPC SIF conversion...")
            tar_target = sif_target.with_suffix(".tar")
            tar_cmd = [selected_oci, "save", image_tag, "-o", str(tar_target)]
            subprocess.run(tar_cmd, check=True)
            console.print(f"[bold green]✔ Saved OCI archive to {tar_target}[/bold green]")
            console.print(f"   [dim]Convert on HPC via: singularity build {sif_target} docker-archive:{tar_target}[/dim]")
        else:
            console.print(f"[bold magenta]📦 Generating HPC SIF image via {selected_sif}: [cyan]{sif_target}[/cyan]...[/bold magenta]")
            source_uri = f"docker-daemon:{image_tag}" if selected_oci == "docker" else f"docker-archive:{sif_target.with_suffix('.tar')}"

            # If using podman, save tar first
            if selected_oci == "podman":
                tar_tmp = sif_target.with_suffix(".podman.tar")
                subprocess.run(["podman", "save", image_tag, "-o", str(tar_tmp)], check=True)
                source_uri = f"docker-archive:{tar_tmp}"

            sif_cmd = [selected_sif, "build", "--force", str(sif_target), source_uri]
            sif_res = subprocess.run(sif_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            if sif_res.returncode != 0:
                console.print(f"[yellow]Warning: {selected_sif} standard build exited with code {sif_res.returncode}. Trying with --fakeroot...[/yellow]")
                sif_cmd = [selected_sif, "build", "--fakeroot", "--force", str(sif_target), source_uri]
                sif_res = subprocess.run(sif_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Cleanup podman temporary tar if created
            if selected_oci == "podman" and Path(f"{sif_target.with_suffix('.podman.tar')}").exists():
                Path(f"{sif_target.with_suffix('.podman.tar')}").unlink()

            if sif_res.returncode == 0:
                sif_size = round(sif_target.stat().st_size / (1024 * 1024), 2)
                console.print(f"[bold green]✔ {selected_sif.capitalize()} SIF created: {sif_target} ({sif_size} MB)[/bold green]")
            else:
                console.print(f"[bold red]❌ Failed to build SIF with {selected_sif}: {sif_res.stderr}[/bold red]")

    return BuildResult(
        tag=image_tag,
        duration=duration,
        size_mb=size_mb,
        sif_path=sif_target,
        oci_engine=selected_oci,
        sif_engine=selected_sif,
        dockerfile=dockerfile_path,
    )


def publish_command(
    engine: str,
    dockerfile: Path,
    context: Path,
    tag: str,
    platforms: Sequence[str],
    source_date_epoch: int,
) -> List[List[str]]:
    """
    Commands that build every platform and push one multi-arch manifest to the registry.

    Layer file mtimes are clamped to SOURCE_DATE_EPOCH (rewrite-timestamp), so a rebuild of
    a locked recipe pushes the same manifest digest instead of a new one.
    """
    if not platforms:
        raise RuntimeError("No target platforms to publish")
    plat = ",".join(platforms)
    sde = str(source_date_epoch)
    if engine == "docker":
        return [[
            "docker", "buildx", "build",
            "--platform", plat,
            "-f", str(dockerfile),
            "--build-arg", f"SOURCE_DATE_EPOCH={sde}",
            "--provenance=false",
            "--output", f"type=image,name={tag},push=true,unpack=false,rewrite-timestamp=true",
            str(context),
        ]]
    if engine == "podman":
        return [
            ["podman", "build", "--platform", plat, "--manifest", tag,
             "-f", str(dockerfile), "--source-date-epoch", sde, "--rewrite-timestamp",
             str(context)],
            ["podman", "manifest", "push", "--all", tag, f"docker://{tag}"],
        ]
    raise RuntimeError(f"Publishing multi-arch images is not supported with '{engine}' (use docker buildx or podman)")


def publish_image(
    dockerfile: Path,
    context: Path,
    tag: str,
    platforms: Sequence[str],
    source_date_epoch: int,
    oci_engine: Optional[str] = None,
) -> None:
    """Rebuild for all platforms and push a reproducible multi-arch image."""
    engine = oci_engine or detect_oci_engine()
    console.print(f"[bold cyan]🚀 Publishing [green]{tag}[/green] for {', '.join(platforms)}...[/bold cyan]")
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    for cmd in publish_command(engine, dockerfile, context, tag, platforms, source_date_epoch):
        res = subprocess.run(cmd, env=env)
        if res.returncode != 0:
            raise RuntimeError(f"Publishing {tag} failed (exit code {res.returncode}): {' '.join(cmd)}")
    console.print(f"[bold green]✔ Successfully pushed {tag} to registry![/bold green]")
