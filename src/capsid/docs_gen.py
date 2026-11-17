"""
GitHub-rendered catalogue (CATALOG.md). The hub website is built by capsid.site.
"""

from typing import Any

from capsid.project import HUB_SITE, REGISTRY


def generate_catalog_markdown(catalog_data: dict[str, Any]) -> str:
    """Generate the Markdown catalogue table shown on GitHub."""
    tools = catalog_data.get("tools", {})
    lines = [
        "# Capsid Hub catalogue",
        "",
        f"{catalog_data.get('total_tools', 0)} tools in {catalog_data.get('total_recipes', 0)} versions, published as "
        f"`{REGISTRY}/<tool>:<version>`. Each version has a page on the [hub website]({HUB_SITE}/) with its locked "
        "closure, digest, usage for Docker, Apptainer, Nextflow and Snakemake, and verification commands.",
        "",
        "| Tool | Versions | Build | Architectures | Licence | Description |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for name, data in sorted(tools.items()):
        versions = list(data.get("versions", {}))
        links = ", ".join(f"[`{v}`]({HUB_SITE}/tools/{name}/{v}/index.html)" for v in versions)
        latest = data["versions"][versions[-1]] if versions else {}
        arch = ", ".join(p.split("/")[-1] for p in latest.get("platforms", []))
        desc = (data.get("description") or "").replace("|", "\\|")
        lines.append(
            f"| [**{name}**]({HUB_SITE}/tools/{name}/index.html) | {links} | `{latest.get('build_type', '')}` "
            f"| {arch} | `{data.get('license', 'NOASSERTION')}` | {desc} |"
        )
    lines += [
        "",
        "Generated from the recipes in this repository; machine-readable index: [catalog.json](catalog.json).",
    ]
    return "\n".join(lines) + "\n"
