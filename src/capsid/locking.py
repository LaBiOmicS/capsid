"""
Lockfiles: pin the complete dependency closure of a recipe or stack.

Layout (next to the definition file, committed to capsid-hub):
    recipes/<tool>/<version>/recipe.yaml  ->  pixi.lock, requirements.lock
    stacks/<name>.yaml                    ->  <name>.pixi.lock, <name>.requirements.lock

Locks are generated inside the same digest-pinned pixi/uv images used by the build,
so the solver that writes the lock is the one that later consumes it.
The pixi manifest is rendered deterministically by Capsid (never by `pixi init`),
so `pixi install --locked` sees exactly the manifest the lock was produced from.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from capsid.models import Platform, Recipe, StackRecipe

PIXI_LOCK = "pixi.lock"
REQUIREMENTS_LOCK = "requirements.lock"

CONDA_PLATFORMS = {
    Platform.LINUX_AMD64: "linux-64",
    Platform.LINUX_ARM64: "linux-aarch64",
}

_SPEC_RE = re.compile(r"^\s*(?:(?P<channel>[A-Za-z0-9_.\-]+)::)?(?P<name>[A-Za-z0-9_.\-]+)\s*(?P<version>.*?)\s*$")


def conda_spec_to_pixi(spec: str) -> tuple[str, str]:
    """Convert a conda match spec ('samtools==1.25', 'bwa=0.7.19', 'bioconda::x') to a pixi.toml entry."""
    m = _SPEC_RE.match(spec)
    if not m:
        raise ValueError(f"Cannot parse conda package spec '{spec}'")
    name, version, channel = m.group("name"), m.group("version").replace(" ", ""), m.group("channel")
    if not version:
        version = "*"
    elif version.startswith("=") and not version.startswith("=="):
        # conda '=1.2' means '1.2.*'
        version = version[1:] if version.endswith("*") else version[1:] + ".*"
    if channel:
        value = f"{{ version = {json.dumps(version)}, channel = {json.dumps(channel)} }}"
    else:
        value = json.dumps(version)
    return name, value


def pixi_manifest(name: str, channels: Sequence[str], packages: Sequence[str], platforms: Iterable[Platform]) -> str:
    """Render a deterministic pixi.toml."""
    conda_platforms = [CONDA_PLATFORMS[Platform(p)] for p in platforms]
    lines = [
        "[workspace]",
        f"name = {json.dumps(name)}",
        f"channels = [{', '.join(json.dumps(c) for c in channels)}]",
        f"platforms = [{', '.join(json.dumps(p) for p in conda_platforms)}]",
        "",
        "[dependencies]",
    ]
    for spec in packages:
        pkg, value = conda_spec_to_pixi(spec)
        lines.append(f"{json.dumps(pkg)} = {value}")
    return "\n".join(lines) + "\n"


def requirements_in(packages: Sequence[str]) -> str:
    return "\n".join(packages) + "\n"


# ---------------------------------------------------------------------------
# Lock file discovery
# ---------------------------------------------------------------------------


def lock_paths(definition: Path) -> tuple[Path, Path]:
    """Return (pixi.lock path, requirements.lock path) for a recipe.yaml or stack.yaml."""
    if definition.name == "recipe.yaml":
        return definition.parent / PIXI_LOCK, definition.parent / REQUIREMENTS_LOCK
    stem = definition.stem
    return definition.parent / f"{stem}.{PIXI_LOCK}", definition.parent / f"{stem}.{REQUIREMENTS_LOCK}"


def attach_lockfiles(model: Recipe | StackRecipe, definition: Path) -> Recipe | StackRecipe:
    """Load sibling lock files (if present) into the model."""
    pixi_path, req_path = lock_paths(definition)
    if pixi_path.is_file():
        model.pixi_lock = pixi_path.read_text()
    if req_path.is_file():
        model.requirements_lock = req_path.read_text()
    return model


# ---------------------------------------------------------------------------
# Lock generation (runs the pinned solver images)
# ---------------------------------------------------------------------------


def _container_cmd(engine: str, image: str, workdir: Path, args: list[str]) -> list[str]:
    cmd = [
        engine,
        "run",
        "--rm",
        "-v",
        f"{workdir}:/w",
        "-w",
        "/w",
        "-e",
        "HOME=/w/.home",
        "-e",
        "PIXI_CACHE_DIR=/w/.cache/pixi",
        "-e",
        "UV_CACHE_DIR=/w/.cache/uv",
    ]
    if engine == "docker" and hasattr(os, "getuid"):
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
    return [*cmd, image, *args]


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Lock command failed ({res.returncode}): {' '.join(cmd)}\n{res.stdout[-4000:]}")


def lock_conda(
    name: str, channels: Sequence[str], packages: Sequence[str], platforms: Iterable[Platform], engine: str
) -> str:
    from capsid.builders.dockerfile_gen import DEFAULT_PIXI_BUILDER

    with tempfile.TemporaryDirectory(prefix="capsid-lock-") as tmp:
        work = Path(tmp)
        os.chmod(work, 0o777)
        (work / "pixi.toml").write_text(pixi_manifest(name, channels, packages, platforms))
        _run(_container_cmd(engine, DEFAULT_PIXI_BUILDER, work, ["pixi", "lock"]))
        return (work / PIXI_LOCK).read_text()


def lock_pypi(packages: Sequence[str], python_version: str, engine: str) -> str:
    """Compile a hashed, universal requirements.lock with the pinned uv binary,
    running inside the same pinned python image the build uses."""
    from capsid.builders.dockerfile_gen import DEFAULT_UV_BUILDER, python_image

    with tempfile.TemporaryDirectory(prefix="capsid-lock-") as tmp:
        work = Path(tmp)
        os.chmod(work, 0o777)
        # Extract /uv from the digest-pinned uv image (it is distroless: no shell, no libc)
        cid = subprocess.run(
            [engine, "create", DEFAULT_UV_BUILDER, "/uv"], capture_output=True, text=True, check=True
        ).stdout.strip()
        try:
            _run([engine, "cp", f"{cid}:/uv", str(work / "uv")])
        finally:
            subprocess.run([engine, "rm", "-f", cid], capture_output=True, check=False)
        (work / "requirements.in").write_text(requirements_in(packages))
        _run(
            _container_cmd(
                engine,
                python_image(python_version),
                work,
                [
                    "/w/uv",
                    "pip",
                    "compile",
                    "requirements.in",
                    "--universal",
                    "--generate-hashes",
                    "--python-version",
                    python_version,
                    "--custom-compile-command",
                    "capsid lock",
                    "-o",
                    REQUIREMENTS_LOCK,
                ],
            )
        )
        return (work / REQUIREMENTS_LOCK).read_text()


def write_locks(definition: Path, pixi_lock: str | None, requirements_lock: str | None) -> list[Path]:
    """Write lock contents next to the definition file; remove stale ones."""
    pixi_path, req_path = lock_paths(definition)
    written = []
    for path, content in ((pixi_path, pixi_lock), (req_path, requirements_lock)):
        if content is not None:
            path.write_text(content)
            written.append(path)
        elif path.exists():
            path.unlink()
    return written


def detect_engine() -> str:
    for engine in ("docker", "podman"):
        if shutil.which(engine):
            return engine
    raise RuntimeError("Generating lockfiles requires docker or podman")
