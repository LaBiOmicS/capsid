"""
RO-Crate (Research Object Crate) Generator:
Produces W3C, BioSchemas, and RO-Crate 1.1 compliant metadata (ro-crate-metadata.json)
for scientific tools packaged by Capsid, ensuring FAIR scientific data principles.
"""

from datetime import UTC, datetime
from typing import Any

from capsid.models import Recipe
from capsid.project import HUB_URL


def generate_ro_crate(recipe: Recipe) -> dict[str, Any]:
    """Generate RO-Crate 1.1 JSON-LD dictionary for a Capsid recipe."""
    # SOURCE_DATE_EPOCH, not "now": the document is embedded in the image and must be reproducible
    now_iso = datetime.fromtimestamp(recipe.build.source_date_epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    root_id = "./"
    software_id = f"#tool-{recipe.name}-{recipe.version}"
    sbom_id = "etc/capsid/sbom.spdx.json"
    recipe_id = "etc/capsid/recipe.yaml"
    module_id = "etc/capsid/module.nf"
    rule_id = "etc/capsid/rule.smk"

    graph: list[dict[str, Any]] = [
        # Metadata descriptor
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            "about": {"@id": root_id},
        },
        # Root Dataset
        {
            "@id": root_id,
            "@type": "Dataset",
            "name": f"Capsid Scientific Container: {recipe.name} (v{recipe.version})",
            "description": recipe.description or f"Hardened, hermetic, FAIR scientific container for {recipe.name}.",
            "datePublished": now_iso,
            "license": recipe.license or "https://spdx.org/licenses/MIT",
            "hasPart": [
                {"@id": software_id},
                {"@id": sbom_id},
                {"@id": recipe_id},
                {"@id": module_id},
                {"@id": rule_id},
            ],
        },
        # Computational Tool / Software Application
        {
            "@id": software_id,
            "@type": ["SoftwareApplication", "ComputationalTool"],
            "name": recipe.name,
            "softwareVersion": str(recipe.version),
            "description": recipe.description or "",
            "url": recipe.homepage or HUB_URL,
            "applicationCategory": recipe.categories,
            "operatingSystem": "Linux (x86_64, aarch64)",
            "softwareRequirements": [f"engine: {recipe.build.type.value}"],
        },
        # Embedded Files
        {
            "@id": sbom_id,
            "@type": ["File", "SoftwareBillOfMaterials"],
            "name": "SPDX 2.3 Software Bill of Materials",
            "encodingFormat": "application/spdx+json",
        },
        {
            "@id": recipe_id,
            "@type": ["File", "ConfigurationFile"],
            "name": "Capsid Recipe Specification",
            "encodingFormat": "text/yaml",
        },
        {
            "@id": module_id,
            "@type": ["File", "SoftwareSourceCode"],
            "name": "Nextflow DSL2 Process Module",
            "encodingFormat": "text/x-groovy",
        },
        {
            "@id": rule_id,
            "@type": ["File", "SoftwareSourceCode"],
            "name": "Snakemake Rule Module",
            "encodingFormat": "text/x-python",
        },
    ]

    # Add Scholarly Publication if DOI is provided
    if recipe.doi:
        clean_doi = recipe.doi.replace("https://doi.org/", "").strip()
        pub_id = f"https://doi.org/{clean_doi}"
        graph.append(
            {
                "@id": pub_id,
                "@type": "ScholarlyArticle",
                "name": f"Primary Publication for {recipe.name}",
                "identifier": clean_doi,
                "url": pub_id,
            }
        )
        # Link software to publication
        graph[2]["citation"] = {"@id": pub_id}

    # Add EDAM Ontology Topics & Operations
    edam_nodes = []
    for topic in recipe.edam_topics:
        tid = f"http://edamontology.org/{topic}"
        edam_nodes.append({"@id": tid})
        graph.append({"@id": tid, "@type": "DefinedTerm", "name": topic, "inDefinedTermSet": "http://edamontology.org"})
    for op in recipe.edam_operations:
        oid = f"http://edamontology.org/{op}"
        edam_nodes.append({"@id": oid})
        graph.append({"@id": oid, "@type": "DefinedTerm", "name": op, "inDefinedTermSet": "http://edamontology.org"})

    if edam_nodes:
        graph[2]["keywords"] = edam_nodes

    if recipe.ro_crate:
        apply_overlay(graph, recipe.ro_crate)

    return {"@context": "https://w3id.org/ro/crate/1.1/context", "@graph": graph}


PROTECTED_ROOT_KEYS = ("@id", "@type", "hasPart")


def apply_overlay(graph: list[dict[str, Any]], overlay: dict[str, Any]) -> None:
    """
    Merge a recipe's `ro_crate` overlay: top-level properties go to the root Dataset
    (except @id, @type and hasPart, which Capsid owns); an optional "@graph" list adds
    entities (e.g. the Person nodes referenced by "author"), replacing any with the same @id.
    """
    root = graph[1]
    for key, value in overlay.items():
        if key == "@graph":
            continue
        if key in PROTECTED_ROOT_KEYS:
            raise ValueError(f"ro_crate overlay may not override '{key}' of the root Dataset")
        root[key] = value
    extra = overlay.get("@graph", [])
    if not isinstance(extra, list) or not all(isinstance(e, dict) and "@id" in e for e in extra):
        raise ValueError("ro_crate overlay '@graph' must be a list of objects with an '@id'")
    for entity in extra:
        graph[:] = [e for e in graph if e.get("@id") != entity["@id"]] + [entity]
