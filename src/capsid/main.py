"""
capsid: The Modern BioContainers CLI.
Fast, multi-arch, slim containers powered by uv, Pixi, and multi-stage BuildKit.
Fully compatible with Docker, Podman, Singularity, Apptainer, Nextflow, and Snakemake.
"""

import subprocess
from pathlib import Path

import httpx
import typer
import yaml
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from capsid.builders.dockerfile_gen import generate_dockerfile
from capsid.builders.runner import build_recipe, publish_image
from capsid.catalog import export_catalog, print_catalog_summary, scan_recipes
from capsid.exporter import export_apptainer_def, export_nextflow_config, export_nextflow_process, export_snakemake_rule
from capsid.locking import attach_lockfiles
from capsid.models import Recipe, StackRecipe, recipe_to_yaml
from capsid.project import HUB_REPO, REGISTRY, VERSION
from capsid.resolver import find_recipe_candidate
from capsid.scaffolders import scaffold_recipe
from capsid.tester import run_test

app = typer.Typer(
    name="capsid",
    help="Capsid: reproducible, signed, multi-architecture containers for bioinformatics tools.",
    add_completion=False,
)
export_app = typer.Typer(
    help="📦 Export Nextflow modules and config, Snakemake rules, Apptainer definitions and SBOMs."
)
app.add_typer(export_app, name="export")

stack_app = typer.Typer(help="📚 Build images that combine several recipes (e.g. samtools + bcftools).")
app.add_typer(stack_app, name="stack")
app.add_typer(stack_app, name="compose", hidden=True)  # deprecated alias of `stack`

bot_app = typer.Typer(help="🤖 Capsid robot: discover new tools and versions on Bioconda and turn them into recipes.")
app.add_typer(bot_app, name="bot")

console = Console()


def _print_version(value: bool) -> None:
    if value:
        print(f"capsid {VERSION}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_print_version, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """Capsid: reproducible, signed, multi-architecture containers for bioinformatics tools."""


def load_recipe(recipe_path: Path) -> Recipe:
    """Load and validate a recipe YAML file."""
    if not recipe_path.exists():
        console.print(f"[bold red]Error: Recipe file '{recipe_path}' not found.[/bold red]")
        raise typer.Exit(code=1)
    try:
        raw = yaml.safe_load(recipe_path.read_text())
        recipe = Recipe.model_validate(raw)
        attach_lockfiles(recipe, recipe_path)
        return recipe
    except (OSError, ValueError, yaml.YAMLError) as e:  # pydantic.ValidationError is a ValueError
        console.print(f"[bold red]Error validating recipe at '{recipe_path}': {e}[/bold red]")
        raise typer.Exit(code=1) from e


def load_stack(stack_file: Path, resolve: bool = True) -> tuple["StackRecipe", list[Recipe]]:
    """Load a stack definition with its lockfiles and (optionally) resolve its component recipes."""
    if not stack_file.is_file():
        console.print(f"[bold red]Stack file '{stack_file}' not found.[/bold red]")
        raise typer.Exit(code=1)
    try:
        stack = StackRecipe.model_validate(yaml.safe_load(stack_file.read_text()))
        attach_lockfiles(stack, stack_file)
    except (OSError, ValueError, yaml.YAMLError) as e:  # pydantic.ValidationError is a ValueError
        console.print(f"[bold red]Error validating stack at '{stack_file}': {e}[/bold red]")
        raise typer.Exit(code=1) from e
    if not resolve:
        return stack, []
    return stack, [resolve_recipe(f"{c.recipe}@{c.version}" if c.version else c.recipe) for c in stack.components]


def resolve_recipe(target: str) -> Recipe:
    """Load an existing recipe path or auto-scaffold from Bioconda/PyPI/GitHub on the fly."""
    p = Path(target)
    if p.exists() and p.is_file():
        return load_recipe(p)

    tool_name = target
    tool_ver = None
    if "@" in target:
        tool_name, tool_ver = target.split("@", 1)
    elif ":" in target and not any(target.startswith(prefix) for prefix in ["pypi:", "bioconda:", "github:"]):
        tool_name, tool_ver = target.split(":", 1)

    # Search local recipe repositories
    candidate = find_recipe_candidate(tool_name, tool_ver)
    if candidate:
        return load_recipe(candidate)

    # Auto-scaffold on the fly
    console.print(f"[bold cyan]🔍 Target '{target}' is not a local file. Auto-resolving from upstream...[/bold cyan]")
    recipe = scaffold_recipe(target)
    out_file = Path(f"recipes/{recipe.name}/{recipe.version}/recipe.yaml")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    yaml_data = recipe_to_yaml(recipe)
    out_file.write_text(yaml_data)
    console.print(f"[bold green]✔ Auto-scaffolded recipe saved to {out_file}[/bold green]")
    return recipe


@app.command()
def init(
    target: str = typer.Argument(
        ..., help="Package source: 'pypi:pkg', 'bioconda:pkg', 'github:owner/repo', or plain name"
    ),
    version: str | None = typer.Option(None, "--version", "-v", help="Explicit version to scaffold"),
    output: Path | None = typer.Option(
        None, "--out", "-o", help="Output recipe path (defaults to recipes/<name>/<version>/recipe.yaml)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print YAML to stdout instead of saving"),
):
    """
    ✨ Generates a recipe from an upstream package (PyPI, Bioconda or a GitHub release).
    """
    console.print(f"[bold cyan]🔍 Inspecting upstream source for [green]{target}[/green]...[/bold cyan]")
    try:
        recipe = scaffold_recipe(target, version=version)
    except (ValueError, httpx.HTTPError) as e:
        console.print(f"[bold red]Failed to scaffold recipe: {e}[/bold red]")
        raise typer.Exit(code=1) from e

    yaml_data = recipe_to_yaml(recipe)

    if dry_run:
        console.print(Syntax(yaml_data, "yaml", theme="monokai", line_numbers=True))
        return

    out_file = output or Path(f"recipes/{recipe.name}/{recipe.version}/recipe.yaml")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(yaml_data)

    console.print(f"[bold green]✔ Successfully created recipe at [cyan]{out_file}[/cyan]![/bold green]")
    console.print(f"   [dim]Tool: {recipe.name} v{recipe.version} | Build Engine: {recipe.build.type.value}[/dim]")


@app.command()
def dockerfile(
    target: str = typer.Argument(
        ..., help="Path to recipe.yaml or package target ('bioconda:samtools', 'pypi:cutadapt')"
    ),
    output: Path | None = typer.Option(None, "--out", "-o", help="Save Dockerfile to specific path"),
):
    """
    📄 Prints (or saves) the Dockerfile generated for a recipe.
    """
    target_path = Path(target)
    if target_path.exists() and target_path.is_file():
        raw = yaml.safe_load(target_path.read_text())
        if isinstance(raw, dict) and "components" in raw:
            stack_dockerfile(stack_file=target_path, output=output)
            return

    recipe = resolve_recipe(target)
    content = generate_dockerfile(recipe)

    if output:
        output.write_text(content)
        console.print(f"[bold green]✔ Saved Dockerfile to {output}[/bold green]")
    else:
        console.print(Syntax(content, "dockerfile", theme="monokai", line_numbers=True))


@app.command()
def build(
    target: str = typer.Argument(
        ..., help="Path to recipe.yaml or package target ('bioconda:samtools', 'pypi:cutadapt')"
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Override container image tag"),
    apptainer: bool = typer.Option(
        False, "--apptainer", "--sif", "-a", help="Also generate an Apptainer/Singularity .sif image for HPC"
    ),
    test_after: bool = typer.Option(True, "--test/--no-test", help="Automatically run verification tests after build"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Build without docker cache"),
    engine: str | None = typer.Option(None, "--engine", "-e", help="OCI build engine ('docker', 'podman', 'nerdctl')"),
    push: bool = typer.Option(False, "--push", "-p", help="Push image to remote container registry (e.g. ghcr.io)"),
):
    """
    🔨 Builds the image with Docker BuildKit or Podman, runs its test, and optionally writes a SIF and pushes.
    """
    target_path = Path(target)
    if target_path.exists() and target_path.is_file():
        raw = yaml.safe_load(target_path.read_text())
        if isinstance(raw, dict) and "components" in raw:
            stack_build(
                stack_file=target_path,
                tag=tag,
                apptainer=apptainer,
                test_after=test_after,
                no_cache=no_cache,
                engine=engine,
                push=push,
            )
            return

    recipe = resolve_recipe(target)
    try:
        res = build_recipe(
            recipe,
            tag=tag,
            build_apptainer=apptainer,
            no_cache=no_cache,
            oci_engine=engine,
        )
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as e:
        console.print(f"[bold red]Build failed: {e}[/bold red]")
        raise typer.Exit(code=1) from e

    if test_after:
        test_res = run_test(recipe, image_tag=res.tag, sif_path=res.sif_path)
        if not test_res.passed:
            console.print("[bold red]Build succeeded but container tests failed![/bold red]")
            raise typer.Exit(code=2)

    # Publish only after the local image passed its tests
    if push:
        try:
            publish_image(
                res.dockerfile,
                res.dockerfile.parent,
                res.tag,
                [p.value for p in recipe.platforms],
                recipe.build.source_date_epoch,
                oci_engine=res.oci_engine,
            )
        except RuntimeError as e:
            console.print(f"[bold red]{e}[/bold red]")
            raise typer.Exit(code=1) from e


@app.command()
def lock(
    target: Path = typer.Argument(..., help="Path to recipe.yaml or stack.yaml"),
):
    """
    🔒 Resolves the full conda/PyPI dependency closure into lockfiles next to the recipe or stack.
    Uses the same digest-pinned pixi/uv images as the build. Commit the generated files.
    """
    from capsid.builders.stack_builder import stack_inputs, stack_platforms
    from capsid.locking import detect_engine, lock_conda, lock_pypi, write_locks
    from capsid.models import BuildType

    if not target.is_file():
        console.print(f"[bold red]File '{target}' not found.[/bold red]")
        raise typer.Exit(code=1)
    raw = yaml.safe_load(target.read_text())
    engine = detect_engine()
    pixi_lock = req_lock = None
    try:
        if isinstance(raw, dict) and "components" in raw:
            stack, resolved = load_stack(target)
            channels, conda, pypi, pyver = stack_inputs(stack, resolved)
            if conda:
                console.print(f"[cyan]🔒 Locking conda packages {conda} via pixi...[/cyan]")
                pixi_lock = lock_conda(stack.name, channels, conda, stack_platforms(resolved), engine)
            if pypi:
                console.print(f"[cyan]🔒 Locking PyPI packages {pypi} via uv (Python {pyver})...[/cyan]")
                req_lock = lock_pypi(pypi, pyver, engine)
        else:
            recipe = load_recipe(target)
            b = recipe.build
            if b.type == BuildType.PIXI_CONDA:
                console.print(
                    f"[cyan]🔒 Locking {b.conda_packages} via pixi for {[p.value for p in recipe.platforms]}...[/cyan]"
                )
                pixi_lock = lock_conda(
                    recipe.name,
                    b.channels or ["conda-forge", "bioconda"],
                    b.conda_packages or [],
                    recipe.platforms,
                    engine,
                )
            elif b.type == BuildType.UV_PYPI and b.package:
                pyver = b.python_version or "3.12"
                console.print(f"[cyan]🔒 Locking {b.package} via uv (Python {pyver})...[/cyan]")
                req_lock = lock_pypi([b.package], pyver, engine)
            else:
                console.print(
                    f"[yellow]Build type '{b.type.value}' has no package lockfile (sha256 pins the source).[/yellow]"
                )
                return
    except RuntimeError as e:
        console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(code=1) from e

    for path in write_locks(target, pixi_lock, req_lock):
        console.print(f"[bold green]✔ Wrote {path}[/bold green]")


@app.command()
def schema(
    out_dir: Path = typer.Option(
        Path("schema"), "--out-dir", "-o", help="Directory for recipe.schema.json / stack.schema.json"
    ),
    check: bool = typer.Option(False, "--check", help="Only verify the files are up to date (exit 1 if not)"),
):
    """
    📐 Writes the JSON Schema of recipe.yaml and stack.yaml (for editors and validators).
    """
    from capsid.schema import dumps, schemas

    stale = []
    for name, sch in schemas().items():
        path = out_dir / name
        content = dumps(sch)
        if path.is_file() and path.read_text() == content:
            continue
        stale.append(path)
        if not check:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            console.print(f"[bold green]✔ Wrote {path}[/bold green]")
    if check and stale:
        console.print(f"[bold red]Out of date: {', '.join(map(str, stale))} -- run `capsid schema`[/bold red]")
        raise typer.Exit(code=1)
    if not stale:
        console.print("[green]Schemas are up to date.[/green]")


@app.command()
def test(
    target: str = typer.Argument(..., help="Path to recipe.yaml, stack.yaml, or package target"),
    image: str | None = typer.Option(
        None, "--image", "-i", help="Image tag to test (defaults to recipe/stack canonical tag)"
    ),
    engine: str | None = typer.Option(
        None, "--engine", "-e", help="Test runtime engine: 'docker', 'podman', 'apptainer', or 'singularity'"
    ),
    sif: Path | None = typer.Option(
        None, "--sif", "-s", help="Explicit path to .sif file for Apptainer/Singularity testing"
    ),
):
    """
    🧪 Runs a recipe's test against an existing image (Docker, Podman, Apptainer or Singularity).
    """
    target_path = Path(target)
    if target_path.exists() and target_path.is_file():
        raw = yaml.safe_load(target_path.read_text())
        if isinstance(raw, dict) and "components" in raw:
            stack_test(stack_file=target_path, image=image, engine=engine, sif=sif)
            return

    recipe = resolve_recipe(target)
    res = run_test(recipe, image_tag=image, engine=engine, sif_path=sif)
    if not res.passed:
        raise typer.Exit(code=1)


@export_app.command("nextflow")
def export_nf(
    target: str = typer.Argument(..., help="Recipe path or target name (e.g. 'samtools@1.25' or 'cutadapt')"),
    registry: str = typer.Option(REGISTRY, "--registry", "-r", help="OCI Registry prefix"),
    output: Path | None = typer.Option(None, "--out", "-o", help="Save Nextflow process to file"),
):
    """
    🧬 Exports a Nextflow DSL2 process module (nf-core style) that uses the image.
    """
    recipe = resolve_recipe(target)
    content = export_nextflow_process(recipe, registry_prefix=registry)

    if output:
        output.write_text(content)
        console.print(f"[bold green]✔ Saved Nextflow module to [cyan]{output}[/cyan][/bold green]")
    else:
        console.print(Syntax(content, "groovy", theme="monokai", line_numbers=True))


@export_app.command("snakemake")
def export_smk(
    target: str = typer.Argument(..., help="Recipe path or target name (e.g. 'bwa@0.7.19')"),
    registry: str = typer.Option(REGISTRY, "--registry", "-r", help="OCI Registry prefix"),
    output: Path | None = typer.Option(None, "--out", "-o", help="Save Snakemake rule to file"),
):
    """
    🐍 Exports a Snakemake rule with a container directive.
    """
    recipe = resolve_recipe(target)
    content = export_snakemake_rule(recipe, registry_prefix=registry)

    if output:
        output.write_text(content)
        console.print(f"[bold green]✔ Saved Snakemake rule to [cyan]{output}[/cyan][/bold green]")
    else:
        console.print(Syntax(content, "python", theme="monokai", line_numbers=True))


@export_app.command("config")
def export_cfg(
    target: str | None = typer.Option(None, "--target", "-t", help="Optional specific recipe target"),
    registry: str = typer.Option(REGISTRY, "--registry", "-r", help="OCI Registry prefix"),
    output: Path | None = typer.Option(None, "--out", "-o", help="Save nextflow.config to file"),
):
    """
    ⚙️ Exports a nextflow.config with Docker, Podman, Singularity, Apptainer and SLURM profiles.
    """
    recipe = resolve_recipe(target) if target else None
    content = export_nextflow_config(recipe=recipe, registry_prefix=registry)

    if output:
        output.write_text(content)
        console.print(f"[bold green]✔ Saved nextflow.config to [cyan]{output}[/cyan][/bold green]")
    else:
        console.print(Syntax(content, "groovy", theme="monokai", line_numbers=True))


@export_app.command("def")
def export_singularity_def(
    target: str = typer.Argument(..., help="Recipe path or target name"),
    output: Path | None = typer.Option(None, "--out", "-o", help="Save Singularity .def file"),
):
    """
    📦 Exports an Apptainer/Singularity definition file that bootstraps from the image.
    """
    recipe = resolve_recipe(target)
    content = export_apptainer_def(recipe)

    if output:
        output.write_text(content)
        console.print(f"[bold green]✔ Saved Apptainer definition to [cyan]{output}[/cyan][/bold green]")
    else:
        console.print(content)


@export_app.command("sbom")
def export_sbom_cmd(
    target: str = typer.Argument(..., help="Recipe path or target name"),
    output: Path | None = typer.Option(None, "--out", "-o", help="Save SPDX 2.3 SBOM JSON file"),
):
    """
    📜 Exports an SPDX 2.3 software bill of materials (SBOM) for a recipe.
    """
    import json

    from capsid.sbom import generate_spdx_sbom

    recipe = resolve_recipe(target)
    sbom_data = generate_spdx_sbom(recipe)
    content = json.dumps(sbom_data, indent=2)

    if output:
        output.write_text(content)
        console.print(f"[bold green]✔ Saved SPDX 2.3 SBOM to [cyan]{output}[/cyan][/bold green]")
    else:
        console.print(Syntax(content, "json", theme="monokai", line_numbers=True))


@stack_app.command("dockerfile")
def stack_dockerfile(
    stack_file: Path = typer.Argument(..., help="Path to stack.yaml definition file"),
    output: Path | None = typer.Option(None, "--out", "-o", help="Save Dockerfile to file"),
):
    """
    📄 Prints (or saves) the Dockerfile generated for a stack.
    """
    from capsid.builders.stack_builder import generate_stack_dockerfile

    stack, resolved = load_stack(stack_file)
    content = generate_stack_dockerfile(stack, resolved)
    if output:
        output.write_text(content)
        console.print(f"[bold green]✔ Saved Stack Dockerfile to [cyan]{output}[/cyan][/bold green]")
    else:
        console.print(Syntax(content, "dockerfile", theme="monokai", line_numbers=True))


@stack_app.command("build")
def stack_build(
    stack_file: Path = typer.Argument(..., help="Path to stack.yaml definition file"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Override container image tag"),
    apptainer: bool = typer.Option(
        False, "--apptainer", "--sif", "-a", help="Also generate an Apptainer/Singularity .sif image"
    ),
    test_after: bool = typer.Option(True, "--test/--no-test", help="Automatically run verification tests after build"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Build without the build cache"),
    engine: str | None = typer.Option(None, "--engine", "-e", help="OCI build engine ('docker', 'podman', 'nerdctl')"),
    push: bool = typer.Option(False, "--push", "-p", help="Push image to remote container registry"),
):
    """
    🔨 Builds one image containing every component of a stack, tests it and optionally pushes it.
    """
    from capsid.builders.dockerfile_gen import DEFAULT_SOURCE_DATE_EPOCH
    from capsid.builders.runner import build_image
    from capsid.builders.stack_builder import generate_stack_dockerfile, stack_platforms

    stack, resolved = load_stack(stack_file)
    console.print(f"[bold cyan]🔨 Building stack [green]{stack.name}[/green] {stack.version}...[/bold cyan]")
    try:
        res = build_image(
            generate_stack_dockerfile(stack, resolved),
            name=stack.name,
            version=stack.version,
            tag=tag or f"{stack.name}:{stack.version}",
            source_date_epoch=DEFAULT_SOURCE_DATE_EPOCH,
            build_apptainer=apptainer,
            no_cache=no_cache,
            oci_engine=engine,
        )
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as e:
        console.print(f"[bold red]Build failed: {e}[/bold red]")
        raise typer.Exit(code=1) from e

    if test_after:
        test_res = run_test(stack, image_tag=res.tag, sif_path=res.sif_path)
        if not test_res.passed:
            console.print("[bold red]Build succeeded but stack tests failed![/bold red]")
            raise typer.Exit(code=2)

    # Publish only after the local image passed its tests
    if push:
        try:
            publish_image(
                res.dockerfile,
                res.dockerfile.parent,
                res.tag,
                [p.value for p in stack_platforms(resolved)],
                DEFAULT_SOURCE_DATE_EPOCH,
                oci_engine=res.oci_engine,
            )
        except RuntimeError as e:
            console.print(f"[bold red]{e}[/bold red]")
            raise typer.Exit(code=1) from e


@stack_app.command("test")
def stack_test(
    stack_file: Path = typer.Argument(..., help="Path to stack.yaml definition file"),
    image: str | None = typer.Option(None, "--image", "-i", help="Image tag to test (defaults to stack name:version)"),
    engine: str | None = typer.Option(
        None, "--engine", "-e", help="Test runtime engine: 'docker', 'podman', 'apptainer', or 'singularity'"
    ),
    sif: Path | None = typer.Option(
        None, "--sif", "-s", help="Explicit path to .sif file for Apptainer/Singularity testing"
    ),
):
    """
    🧪 Runs a stack's test against an existing image.
    """
    stack, _ = load_stack(stack_file, resolve=False)
    res = run_test(stack, image_tag=image, engine=engine, sif_path=sif)
    if not res.passed:
        raise typer.Exit(code=1)


@app.command()
def catalog(
    recipes_dir: Path = typer.Option(Path("recipes"), "--dir", "-d", help="Recipes directory to scan"),
    output: Path | None = typer.Option(Path("catalog.json"), "--out", "-o", help="Output catalog.json path"),
):
    """
    📚 Indexes all recipes into catalog.json (image references and workflow snippets per version).
    """
    recipes = scan_recipes(recipes_dir)
    if not recipes:
        console.print(f"[yellow]No recipes found in {recipes_dir}[/yellow]")
        return

    print_catalog_summary(recipes)
    if output:
        export_catalog(recipes_dir, output)
        console.print(f"[bold green]✔ Catalog saved to [cyan]{output}[/cyan][/bold green]")


@app.command("docs")
def generate_docs(
    recipes_dir: Path = typer.Option(Path("recipes"), "--dir", "-d", help="Recipes directory to scan"),
    catalog_path: Path = typer.Option(Path("catalog.json"), "--catalog", "-c", help="Path to catalog.json"),
    out_dir: Path = typer.Option(Path("."), "--out-dir", "-o", help="Output directory for CATALOG.md and catalog.html"),
):
    """
    🌐 Generates CATALOG.md and the searchable catalog.html from catalog.json.
    """
    import json

    from capsid.docs_gen import generate_catalog_html, generate_catalog_markdown

    if not catalog_path.exists():
        console.print(f"[cyan]Compiling catalog from {recipes_dir}...[/cyan]")
        cat = export_catalog(recipes_dir, catalog_path)
    else:
        cat = json.loads(catalog_path.read_text())

    md_content = generate_catalog_markdown(cat)
    md_file = out_dir / "CATALOG.md"
    md_file.write_text(md_content)

    html_content = generate_catalog_html(cat)
    html_file = out_dir / "catalog.html"
    html_file.write_text(html_content)

    console.print(f"[bold green]✔ Generated Markdown registry at [cyan]{md_file}[/cyan][/bold green]")
    console.print(f"[bold green]✔ Generated Interactive Web Catalog at [cyan]{html_file}[/cyan][/bold green]")


@app.command()
def lint(
    target: Path = typer.Argument(Path("recipes"), help="recipe.yaml, stack .yaml, or a directory to scan for both"),
    catalog_file: Path | None = typer.Option(
        None,
        "--catalog",
        "-c",
        help="catalog.json of the published hub; flags versions that are already published (W040)",
    ),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors"),
):
    """
    🛡️ Lints recipes and stacks: schema, layout, pinning, lockfiles, checksums, dangerous commands and tests.
    """
    from capsid.linter import is_stack_file, lint_path

    target_files: list[Path] = []
    if target.is_file():
        target_files.append(target)
    elif target.is_dir():
        target_files.extend(f for f in sorted(target.rglob("*.yaml")) if f.name == "recipe.yaml" or is_stack_file(f))
    else:
        console.print(f"[bold red]Target path '{target}' not found.[/bold red]")
        raise typer.Exit(code=1)

    if not target_files:
        console.print(f"[yellow]No recipes or stacks found in '{target}'[/yellow]")
        return

    console.print(
        f"[bold cyan]🔍 Auditing {len(target_files)} file(s) for security, schema, and best practices...[/bold cyan]"
    )

    total_errors = 0
    total_warnings = 0

    for tf in target_files:
        issues = lint_path(tf, catalog_path=catalog_file)
        if issues:
            console.print(f"\n[bold]{tf}[/bold]:")
            for issue in issues:
                console.print(f"  {issue}")
                if issue.level == "ERROR":
                    total_errors += 1
                else:
                    total_warnings += 1

    if strict:
        total_errors, total_warnings = total_errors + total_warnings, 0
    if total_errors > 0:
        console.print(
            f"\n[bold red]❌ Audit FAILED with {total_errors} error(s) and {total_warnings} warning(s).[/bold red]"
        )
        raise typer.Exit(code=1)
    elif total_warnings > 0:
        console.print(f"\n[bold yellow]✔ Audit PASSED with {total_warnings} warning(s). Zero errors.[/bold yellow]")
    else:
        console.print(
            f"\n[bold green]✔ All {len(target_files)} file(s) passed audit with zero errors or warnings![/bold green]"
        )


@app.command()
def submit(
    target: str = typer.Argument(
        ..., help="Recipe file path or target (e.g. 'recipes/samtools/1.25/recipe.yaml' or 'bioconda:samtools@1.25')"
    ),
    upstream: str = typer.Option(HUB_REPO, "--upstream", "-u", help="Upstream Capsid Hub repository (owner/repo)"),
    web: bool = typer.Option(False, "--web", "-w", help="Generate 1-click web submission form instead of Git PR"),
):
    """
    🚀 Lints a recipe and submits it to Capsid Hub as a pull request (or a pre-filled issue).
    """
    from capsid.publisher import submit_recipe

    recipe = resolve_recipe(target)
    recipe_path = Path(f"recipes/{recipe.name}/{recipe.version}/recipe.yaml")

    if not recipe_path.exists():
        # Check alternative locations
        found = find_recipe_candidate(recipe.name, recipe.version)
        if found:
            recipe_path = found
        else:
            console.print(f"[bold red]Recipe file {recipe_path} could not be located.[/bold red]")
            raise typer.Exit(code=1)

    try:
        submit_recipe(recipe, recipe_path=recipe_path, upstream_repo=upstream, force_web=web)
    except (ValueError, OSError) as e:
        console.print(f"[bold red]Submission aborted: {e}[/bold red]")
        raise typer.Exit(code=1) from e


@app.command()
def sync(
    upstream: str = typer.Option(HUB_REPO, "--upstream", "-u", help="Upstream Capsid Hub repository (owner/repo)"),
    recipes_dir: Path = typer.Option(Path("recipes"), "--dir", "-d", help="Local recipes directory"),
    catalog_path: Path = typer.Option(Path("catalog.json"), "--catalog", "-c", help="Local catalog.json path"),
):
    """
    🔄 Downloads the recipes published on Capsid Hub that are missing locally and reindexes the catalog.
    """
    from capsid.syncer import sync_hub

    sync_hub(upstream_repo=upstream, recipes_dir=recipes_dir, catalog_path=catalog_path)


@app.command("import-bioconda")
def import_bioconda(
    package: str = typer.Argument(..., help="Bioconda package name to import"),
    version: str | None = typer.Option(None, "--version", "-v", help="Version to import (default: latest)"),
    output: Path | None = typer.Option(None, "--out", "-o", help="Output recipe path"),
):
    """
    🔄 Generates a recipe from a Bioconda package (same as `capsid init bioconda:<package>`).
    """
    init(target=f"bioconda:{package}", version=version, output=output, dry_run=False)


@bot_app.command("check")
def bot_check(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window in days (e.g. 7 for Friday weekly sync)"),
    limit: int = typer.Option(15, "--limit", "-l", help="Maximum number of candidates to inspect"),
    tools: str | None = typer.Option(None, "--tools", "-t", help="Comma-separated tool names to check explicitly"),
    recipes_dir: Path = typer.Option(Path("recipes"), "--recipes-dir", "-r", help="Path to local recipes directory"),
):
    """
    🔎 Lists new tools and versions on Bioconda without writing any file.
    """
    from capsid.bot import fetch_upstream_candidates

    tool_list = [t.strip() for t in tools.split(",")] if tools else None
    candidates = fetch_upstream_candidates(days=days, limit=limit, specific_tools=tool_list, recipes_dir=recipes_dir)

    if not candidates:
        console.print("[green]✔ No new tools or updates detected upstream. Everything is up to date![/green]")
        return

    table = Table(
        title=f"🔎 Upstream Candidates Discovered (Last {days} days)", show_header=True, header_style="bold cyan"
    )
    table.add_column("Tool", style="bold")
    table.add_column("Version")
    table.add_column("Action")
    table.add_column("Current Local")
    table.add_column("License")
    table.add_column("Summary", max_width=45)

    for c in candidates:
        action_style = "green" if c.action == "NEW_TOOL" else "yellow"
        table.add_row(
            c.name,
            c.version,
            f"[{action_style}]{c.action}[/{action_style}]",
            c.current_local_version or "-",
            c.license,
            c.summary[:45] + ("..." if len(c.summary) > 45 else ""),
        )

    console.print(table)
    console.print("\n[dim]Run 'capsid bot sync' to transform these into Capsid recipes.[/dim]\n")


@bot_app.command("sync")
def bot_sync(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window in days (e.g. 7 for Friday weekly sync)"),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum number of tools to ingest per run"),
    tools: str | None = typer.Option(None, "--tools", "-t", help="Comma-separated tool names to ingest"),
    recipes_dir: Path = typer.Option(Path("recipes"), "--recipes-dir", "-r", help="Path to local recipes directory"),
    catalog_path: Path = typer.Option(Path("catalog.json"), "--catalog", "-c", help="Path to catalog.json"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate transformations without writing to disk"),
    out_report: Path | None = typer.Option(
        None, "--report", help="Save markdown report to file (e.g. for GitHub Actions PR)"
    ),
):
    """
    🤖 Runs the Capsid robot: discovers upstream updates, turns them into recipes, lints them and updates the catalog.
    """
    from capsid.bot import run_robot_pipeline

    tool_list = [t.strip() for t in tools.split(",")] if tools else None

    _, report = run_robot_pipeline(
        days=days,
        limit=limit,
        specific_tools=tool_list,
        recipes_dir=recipes_dir,
        catalog_path=catalog_path,
        dry_run=dry_run,
        update_catalog=True,
    )

    if out_report:
        out_report.parent.mkdir(parents=True, exist_ok=True)
        out_report.write_text(report)
        console.print(f"[dim]Saved report to {out_report}[/dim]")


@bot_app.command("migrate-all")
def bot_migrate_all(
    category: str = typer.Option(
        "core",
        "--category",
        "-c",
        help="Category: 'core' (~7,006 tools), 'r' (~5,161 packages), 'perl' (~736 packages), or 'all' (12,903)",
    ),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Limit number of tools to convert"),
    recipes_dir: Path = typer.Option(Path("recipes"), "--recipes-dir", "-r", help="Path to local recipes directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without writing files to disk"),
    update_catalog: bool = typer.Option(False, "--update-catalog", help="Update catalog.json after completion"),
):
    """
    🚀 Converts Bioconda packages in bulk into Capsid recipes (review before submitting).
    """
    from capsid.bot import migrate_bioconda_all

    migrate_bioconda_all(
        recipes_dir=recipes_dir, category=category.lower(), limit=limit, dry_run=dry_run, update_catalog=update_catalog
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
