"""
Documentation & Web Catalog Generator:
Compiles an interactive Single Page Application (catalog.html) and GitHub CATALOG.md
with instant search, category filters, and 1-click Nextflow and Snakemake snippet copy.
"""

import json
from typing import Any

from capsid.project import REGISTRY


def generate_catalog_markdown(catalog_data: dict[str, Any]) -> str:
    """Generate GitHub-friendly Markdown catalog table and documentation."""
    tools = catalog_data.get("tools", {})
    total_recipes = catalog_data.get("total_recipes", 0)
    total_tools = catalog_data.get("total_tools", 0)

    lines = [
        "# 📚 Capsid Hub Tool Registry",
        "",
        "> Ultra-slim, hardened, rootless scientific containers ready for Docker, Apptainer/Singularity, Nextflow, and Snakemake.",
        "",
        f"**Tools:** {total_tools} | **Versions:** {total_recipes} | "
        "**Images:** multi-architecture, Cosign-signed, with SPDX SBOM and SLSA provenance",
        "",
        "| Tool | Versions | Engine | Target Architectures | License | Description | Nextflow Snippet | Snakemake Snippet |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for tool_name, data in sorted(tools.items()):
        versions = list(data.get("versions", {}).keys())
        ver_str = ", ".join(f"`{v}`" for v in versions)
        latest_ver = versions[-1] if versions else "latest"
        ver_info = data.get("versions", {}).get(latest_ver, {})
        engine = ver_info.get("build_type", "pixi-conda")
        platforms = ", ".join(p.split("/")[-1] for p in ver_info.get("platforms", []))
        lic = data.get("license", "UNKNOWN")
        desc = (data.get("description", "") or "")[:50]
        nf_cmd = f"`capsid export nextflow {tool_name}@{latest_ver}`"
        smk_cmd = f"`capsid export snakemake {tool_name}@{latest_ver}`"

        lines.append(
            f"| **`{tool_name}`** | {ver_str} | `{engine}` | `{platforms}` | `{lic}` | {desc} | {nf_cmd} | {smk_cmd} |"
        )

    lines.extend(
        [
            "",
            "---",
            "## 🚀 Quick Usage in Pipelines",
            "",
            "### Nextflow DSL2",
            "```groovy",
            "process SAMTOOLS {",
            f"    container '{REGISTRY}/samtools:1.25'",
            "    script:",
            '    "samtools view -h in.bam > out.sam"',
            "}",
            "```",
            "",
            "### Snakemake",
            "```python",
            "rule bwa_mem:",
            f"    container: 'docker://{REGISTRY}/bwa:0.7.19'",
            "    shell: 'bwa mem {input} > {output}'",
            "```",
            "",
            "### Apptainer / HPC Direct",
            "```bash",
            f"apptainer exec docker://{REGISTRY}/samtools:1.25 samtools --version",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_catalog_html(catalog_data: dict[str, Any]) -> str:
    """Generate standalone interactive HTML catalog with search and filters."""
    cat_json = json.dumps(catalog_data)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Capsid Hub | Modern BioContainers Registry</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --purple: #bc8cff;
      --yellow: #d29922;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2rem 1rem;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    header {{ text-align: center; margin-bottom: 2.5rem; }}
    h1 {{ font-size: 2.5rem; font-weight: 700; color: #fff; margin-bottom: 0.5rem; display: flex; align-items: center; justify-content: center; gap: 0.5rem; }}
    .subtitle {{ color: var(--text-muted); font-size: 1.1rem; max-width: 700px; margin: 0 auto 1.5rem; }}
    .stats-bar {{ display: flex; justify-content: center; gap: 2rem; margin-bottom: 2rem; }}
    .stat-badge {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 0.5rem 1rem; font-size: 0.9rem; }}
    .stat-badge strong {{ color: var(--accent); }}
    .search-box {{
      width: 100%;
      max-width: 600px;
      margin: 0 auto 2rem;
      position: relative;
    }}
    input[type="text"] {{
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.8rem 1.2rem;
      color: #fff;
      font-size: 1rem;
      outline: none;
      transition: border-color 0.2s;
    }}
    input[type="text"]:focus {{ border-color: var(--accent); }}
    .tool-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 1.5rem; }}
    .tool-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.15s, border-color 0.15s;
    }}
    .tool-card:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
    .tool-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.8rem; }}
    .tool-name {{ font-size: 1.3rem; font-weight: 600; color: #fff; }}
    .badge {{
      display: inline-block;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
    }}
    .badge-pixi {{ background: rgba(88, 166, 255, 0.15); color: var(--accent); border: 1px solid var(--accent); }}
    .badge-uv {{ background: rgba(63, 185, 80, 0.15); color: var(--green); border: 1px solid var(--green); }}
    .tool-desc {{ color: var(--text-muted); font-size: 0.9rem; margin-bottom: 1rem; min-height: 40px; }}
    .versions-list {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 1.2rem; }}
    .version-tag {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.15rem 0.4rem;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.8rem;
    }}
    .btn-group {{ display: flex; gap: 0.5rem; }}
    button.btn {{
      background: rgba(255, 255, 255, 0.08);
      color: #fff;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.4rem 0.8rem;
      font-size: 0.8rem;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.3rem;
      transition: background 0.2s;
    }}
    button.btn:hover {{ background: rgba(255, 255, 255, 0.15); }}
    .toast {{
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: var(--green);
      color: #000;
      font-weight: 600;
      padding: 0.6rem 1.2rem;
      border-radius: 6px;
      display: none;
      animation: fadeIn 0.3s;
    }}
    @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🧬 Capsid Hub</h1>
      <p class="subtitle">Next-generation container factory for bioinformatics. Ultra-slim, hardened rootless OCI and SIF images with native Nextflow & Snakemake integration.</p>
      <div class="stats-bar">
        <div class="stat-badge">Total Tools: <strong id="stat-tools">0</strong></div>
        <div class="stat-badge">Total Recipes: <strong id="stat-recipes">0</strong></div>
        <div class="stat-badge">Engines: <strong>uv (PyPI) &amp; pixi (conda)</strong></div>
        <div class="stat-badge">HPC: <strong>Apptainer & Singularity SIF</strong></div>
      </div>
      <div class="search-box">
        <input type="text" id="search" placeholder="Search tool by name, category, or description (e.g. samtools, bwa, alignment)..." onkeyup="filterTools()">
      </div>
    </header>

    <div class="tool-grid" id="grid"></div>
  </div>

  <div class="toast" id="toast">Copied to clipboard!</div>

  <script>
    const data = {cat_json};
    const tools = data.tools || {{}};

    document.getElementById('stat-tools').innerText = Object.keys(tools).length;
    document.getElementById('stat-recipes').innerText = data.total_recipes || Object.keys(tools).length;

    function renderTools(filter = '') {{
      const grid = document.getElementById('grid');
      grid.innerHTML = '';
      const query = filter.toLowerCase().trim();

      for (const [name, info] of Object.entries(tools)) {{
        const desc = info.description || '';
        const match = name.toLowerCase().includes(query) || desc.toLowerCase().includes(query);
        if (!match) continue;

        const versions = Object.keys(info.versions || {{}});
        const latest = versions[versions.length - 1] || '1.0';
        const latestData = (info.versions && info.versions[latest]) || {{}};
        const engine = latestData.build_type || 'pixi-conda';

        const card = document.createElement('div');
        card.className = 'tool-card';
        card.innerHTML = `
          <div>
            <div class="tool-header">
              <span class="tool-name">${{name}}</span>
              <span class="badge ${{engine.includes('uv') ? 'badge-uv' : 'badge-pixi'}}">${{engine}}</span>
            </div>
            <div class="tool-desc">${{desc}}</div>
            <div class="versions-list">
              ${{versions.map(v => `<span class="version-tag">v${{v}}</span>`).join('')}}
            </div>
          </div>
          <div class="btn-group">
            <button class="btn" onclick="copyNf('${{name}}', '${{latest}}')">📋 Nextflow</button>
            <button class="btn" onclick="copySmk('${{name}}', '${{latest}}')">🐍 Snakemake</button>
            <button class="btn" onclick="copySif('${{name}}', '${{latest}}')">📦 Apptainer</button>
          </div>
        `;
        grid.appendChild(card);
      }}
    }}

    function filterTools() {{
      const q = document.getElementById('search').value;
      renderTools(q);
    }}

    function showToast(msg) {{
      const t = document.getElementById('toast');
      t.innerText = msg;
      t.style.display = 'block';
      setTimeout(() => {{ t.style.display = 'none'; }}, 2000);
    }}

    function copyNf(tool, ver) {{
      const snippet = `container '{REGISTRY}/${{tool}}:${{ver}}'`;
      navigator.clipboard.writeText(snippet);
      showToast(`Copied Nextflow directive for ${{tool}}!`);
    }}

    function copySmk(tool, ver) {{
      const snippet = `container: "docker://{REGISTRY}/${{tool}}:${{ver}}"`;
      navigator.clipboard.writeText(snippet);
      showToast(`Copied Snakemake directive for ${{tool}}!`);
    }}

    function copySif(tool, ver) {{
      const snippet = `apptainer exec docker://{REGISTRY}/${{tool}}:${{ver}} ${{tool}} --version`;
      navigator.clipboard.writeText(snippet);
      showToast(`Copied Apptainer command for ${{tool}}!`);
    }}

    renderTools();
  </script>
</body>
</html>
"""
    return html
