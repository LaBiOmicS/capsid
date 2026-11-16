"""
SBOM (Software Bill of Materials) Generator:
Generates SPDX 2.3 compliant SBOM JSON for Capsid recipes to ensure
FAIR scientific data principles, vulnerability scanning, and supply chain transparency.
"""

import json
from datetime import datetime, timezone
from typing import Dict, Any
from capsid.models import Recipe, BuildType


def generate_spdx_sbom(recipe: Recipe) -> Dict[str, Any]:
    """Generate SPDX 2.3 JSON Software Bill of Materials for a recipe."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    spdx_id = f"SPDXRef-Package-{recipe.name}-{recipe.version}".replace(".", "-").replace("_", "-")

    # Determine packages based on build engine
    packages = [
        {
            "SPDXID": spdx_id,
            "name": recipe.name,
            "versionInfo": recipe.version,
            "downloadLocation": recipe.homepage or "NOASSERTION",
            "licenseConcluded": recipe.license or "NOASSERTION",
            "licenseDeclared": recipe.license or "NOASSERTION",
            "supplier": f"Organization: Capsid Community ({recipe.homepage or 'https://github.com/capsid'})",
            "description": recipe.description or "",
            "filesAnalyzed": False
        }
    ]

    # Add upstream dependencies
    if recipe.build.type == BuildType.PIXI_CONDA:
        for idx, pkg in enumerate(recipe.build.conda_packages or []):
            pkg_name = pkg.split("=")[0]
            pkg_ver = pkg.split("=")[-1] if "=" in pkg else "latest"
            packages.append({
                "SPDXID": f"SPDXRef-Package-conda-{pkg_name}-{idx}",
                "name": pkg_name,
                "versionInfo": pkg_ver,
                "downloadLocation": "https://anaconda.org/bioconda/" + pkg_name,
                "licenseConcluded": "NOASSERTION",
                "filesAnalyzed": False
            })
    elif recipe.build.type == BuildType.UV_PYPI:
        pkg_spec = recipe.build.package or recipe.name
        pkg_name = pkg_spec.split("==")[0]
        pkg_ver = pkg_spec.split("==")[-1] if "==" in pkg_spec else recipe.version
        packages.append({
            "SPDXID": f"SPDXRef-Package-pypi-{pkg_name}",
            "name": pkg_name,
            "versionInfo": pkg_ver,
            "downloadLocation": f"https://pypi.org/project/{pkg_name}/",
            "licenseConcluded": recipe.license or "NOASSERTION",
            "filesAnalyzed": False
        })

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"Capsid-SBOM-{recipe.name}-{recipe.version}",
        "documentNamespace": f"https://capsid.bio/sbom/{recipe.name}/{recipe.version}",
        "creationInfo": {
            "created": timestamp,
            "creators": ["Tool: Capsid Engine-0.1.0", "Organization: Capsid Project"]
        },
        "packages": packages,
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": spdx_id
            }
        ]
    }
