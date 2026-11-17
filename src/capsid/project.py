"""
Where Capsid lives: GitHub repositories and the OCI registry namespace.

Single source of truth -- every default URL, registry prefix and upstream repository
is derived from here (the `capsid` GitHub organisation is NOT ours).
"""

ENGINE_REPO = "LaBiOmicS/capsid"
HUB_REPO = "LaBiOmicS/capsid-hub"
ENGINE_URL = f"https://github.com/{ENGINE_REPO}"
HUB_URL = f"https://github.com/{HUB_REPO}"

# Images are published as <REGISTRY>/<tool>:<version>
REGISTRY = "ghcr.io/labiomics/capsid"

VENDOR = "LaBiOmicS Capsid Project"

# Engine version recorded in image labels and SBOMs (keep equal to pyproject.toml)
VERSION = "0.1.0"

# Hub website (GitHub Pages), built by `capsid site`
HUB_SITE = "https://labiomics.github.io/capsid-hub"
