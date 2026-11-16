"""
Build runner: builds images with Docker BuildKit, Podman or nerdctl, converts them to SIF with
Apptainer/Singularity, and publishes multi-architecture manifests.
"""

import os
import shutil
import subprocess
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console

from capsid.builders.dockerfile_gen import generate_dockerfile
from capsid.models import Recipe

console = Console()


class BuildResult:
    def __init__(
        self,
        tag: str,
        duration: float,
        size_mb: float | None = None,
        sif_path: Path | None = None,
        oci_engine: str = "docker",
        sif_engine: str | None = None,
        dockerfile: Path = Path("Dockerfile"),
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
        "No OCI container engine found (checked: docker, podman, nerdctl). Please install Docker or Podman."
    )


def detect_sif_engine() -> str | None:
    """Detect available SIF builder (apptainer, singularity)."""
    for engine in ["apptainer", "singularity"]:
        if shutil.which(engine):
            return engine
    return None


def get_image_size_mb(tag: str, engine: str = "docker") -> float | None:
    """Inspect container image size in Megabytes."""
    try:
        res = subprocess.run(
            [engine, "image", "inspect", tag, "--format={{.Size}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        bytes_val = int(res.stdout.strip())
        return round(bytes_val / (1024 * 1024), 2)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def build_recipe(
    recipe: Recipe,
    tag: str | None = None,
    build_apptainer: bool = False,
    output_sif: Path | None = None,
    no_cache: bool = False,
    build_dir: Path | None = None,
    oci_engine: str | None = None,
) -> BuildResult:
    """Render a recipe's Dockerfile and build it (see build_image)."""
    console.print(f"[bold cyan]🔨 Building [green]{recipe.name}[/green] {recipe.version}...[/bold cyan]")
    console.print(f"   [dim]Build type: {recipe.build.type.value}[/dim]")
    return build_image(
        generate_dockerfile(recipe),
        name=recipe.name,
        version=recipe.version,
        tag=tag or recipe.canonical_tag,
        source_date_epoch=recipe.build.source_date_epoch,
        build_apptainer=build_apptainer,
        output_sif=output_sif,
        no_cache=no_cache,
        build_dir=build_dir,
        oci_engine=oci_engine,
    )


def build_image(
    dockerfile_content: str,
    name: str,
    version: str,
    tag: str,
    source_date_epoch: int,
    build_apptainer: bool = False,
    output_sif: Path | None = None,
    no_cache: bool = False,
    build_dir: Path | None = None,
    oci_engine: str | None = None,
) -> BuildResult:
    """
    Build an image for the host architecture with Docker BuildKit, Podman or nerdctl, and
    optionally convert it to a SIF file with Apptainer/Singularity.

    The Dockerfile is written to .capsid-build/<name>/Dockerfile (the build context).
    """
    selected_oci = oci_engine or detect_oci_engine()
    start_time = time.time()

    work_dir = build_dir or Path(f".capsid-build/{name}")
    work_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = work_dir / "Dockerfile"
    dockerfile_path.write_text(dockerfile_content)
    console.print(f"   [dim]Engine: {selected_oci} | Tag: {tag} | Dockerfile: {dockerfile_path}[/dim]")

    cmd = [
        selected_oci,
        "build",
        "-t",
        tag,
        "-f",
        str(dockerfile_path),
        # BuildKit only honours SOURCE_DATE_EPOCH when passed as a build-arg
        "--build-arg",
        f"SOURCE_DATE_EPOCH={source_date_epoch}",
    ]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(str(work_dir))

    env = os.environ.copy()
    if selected_oci == "docker":
        env["DOCKER_BUILDKIT"] = "1"

    # Show progress markers live; keep the tail of the log to explain a failure
    tail: deque[str] = deque(maxlen=60)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env) as process:
        assert process.stdout is not None  # stdout=PIPE
        for line in process.stdout:
            stripped = line.rstrip()
            tail.append(stripped)
            if any(k in stripped for k in ("Step", "CACHED", "DONE", "ERROR", "naming to", "Writing")):
                console.print(f"   [dim]{stripped.strip()}[/dim]", highlight=False)
        return_code = process.wait()

    if return_code != 0:
        console.print("\n".join(tail), markup=False, highlight=False)
        raise RuntimeError(f"{selected_oci} build failed with exit code {return_code} (Dockerfile: {dockerfile_path})")

    duration = time.time() - start_time
    size_mb = get_image_size_mb(tag, engine=selected_oci)
    console.print(f"[bold green]✔ Image built with {selected_oci} in {duration:.1f}s ({size_mb} MB)[/bold green]")

    sif_target = None
    selected_sif = None
    if build_apptainer:
        sif_target = output_sif or Path(f"{name}-{version}.sif")
        selected_sif = build_sif(tag, sif_target, selected_oci)
        if not selected_sif:
            sif_target = None

    return BuildResult(
        tag=tag,
        duration=duration,
        size_mb=size_mb,
        sif_path=sif_target,
        oci_engine=selected_oci,
        sif_engine=selected_sif,
        dockerfile=dockerfile_path,
    )


def build_sif(image_tag: str, sif_target: Path, oci_engine: str) -> str | None:
    """
    Convert a local image to a SIF file. Returns the SIF engine used, or None when no
    Apptainer/Singularity is installed (an OCI archive is saved instead for conversion elsewhere)
    or the conversion failed.
    """
    selected_sif = detect_sif_engine()
    archive = sif_target.with_suffix(".oci.tar")
    if not selected_sif:
        console.print("[yellow]Notice: Neither 'apptainer' nor 'singularity' found on PATH.[/yellow]")
        subprocess.run([oci_engine, "save", image_tag, "-o", str(archive)], check=True)
        console.print(f"[bold green]✔ Saved image archive to {archive}[/bold green]")
        console.print(
            f"   [dim]Convert it where Apptainer is available: apptainer build {sif_target} "
            f"docker-archive:{archive}[/dim]"
        )
        return None

    console.print(f"[bold magenta]📦 Building SIF with {selected_sif}: [cyan]{sif_target}[/cyan][/bold magenta]")
    # Docker images are read from the daemon; other engines go through a temporary archive
    if oci_engine == "docker":
        source_uri = f"docker-daemon:{image_tag}"
    else:
        subprocess.run([oci_engine, "save", image_tag, "-o", str(archive)], check=True)
        source_uri = f"docker-archive:{archive}"
    try:
        res = subprocess.run(
            [selected_sif, "build", "--force", str(sif_target), source_uri], capture_output=True, text=True, check=False
        )
        if res.returncode != 0:
            console.print(
                f"[yellow]{selected_sif} build exited with {res.returncode}; retrying with --fakeroot[/yellow]"
            )
            res = subprocess.run(
                [selected_sif, "build", "--fakeroot", "--force", str(sif_target), source_uri],
                capture_output=True,
                text=True,
                check=False,
            )
    finally:
        if oci_engine != "docker":
            archive.unlink(missing_ok=True)

    if res.returncode != 0:
        console.print(f"[bold red]❌ Failed to build SIF with {selected_sif}: {res.stderr}[/bold red]")
        return None
    sif_size = round(sif_target.stat().st_size / (1024 * 1024), 2)
    console.print(f"[bold green]✔ SIF created with {selected_sif}: {sif_target} ({sif_size} MB)[/bold green]")
    return selected_sif


def publish_command(
    engine: str,
    dockerfile: Path,
    context: Path,
    tag: str,
    platforms: Sequence[str],
    source_date_epoch: int,
) -> list[list[str]]:
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
        return [
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                plat,
                "-f",
                str(dockerfile),
                "--build-arg",
                f"SOURCE_DATE_EPOCH={sde}",
                "--provenance=false",
                "--output",
                f"type=image,name={tag},push=true,unpack=false,rewrite-timestamp=true",
                str(context),
            ]
        ]
    if engine == "podman":
        return [
            [
                "podman",
                "build",
                "--platform",
                plat,
                "--manifest",
                tag,
                "-f",
                str(dockerfile),
                "--source-date-epoch",
                sde,
                "--rewrite-timestamp",
                str(context),
            ],
            ["podman", "manifest", "push", "--all", tag, f"docker://{tag}"],
        ]
    raise RuntimeError(f"Publishing multi-arch images is not supported with '{engine}' (use docker buildx or podman)")


def publish_image(
    dockerfile: Path,
    context: Path,
    tag: str,
    platforms: Sequence[str],
    source_date_epoch: int,
    oci_engine: str | None = None,
) -> None:
    """Rebuild for all platforms and push a reproducible multi-arch image."""
    engine = oci_engine or detect_oci_engine()
    console.print(f"[bold cyan]🚀 Publishing [green]{tag}[/green] for {', '.join(platforms)}...[/bold cyan]")
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    for cmd in publish_command(engine, dockerfile, context, tag, platforms, source_date_epoch):
        res = subprocess.run(cmd, env=env, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"Publishing {tag} failed (exit code {res.returncode}): {' '.join(cmd)}")
    console.print(f"[bold green]✔ Successfully pushed {tag} to registry![/bold green]")
