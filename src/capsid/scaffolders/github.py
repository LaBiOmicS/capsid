"""
GitHub Scaffolder: inspects GitHub repositories and releases to generate build recipes.
"""

import hashlib
import re
from typing import Optional, Tuple
import httpx

from capsid.models import Recipe, BuildConfig, BuildType, TestConfig, Platform


def parse_github_repo(input_str: str) -> Tuple[str, str]:
    """Parse 'owner/repo' or 'https://github.com/owner/repo'."""
    clean = input_str.strip().rstrip("/")
    if "github.com/" in clean:
        match = re.search(r"github\.com/([^/]+)/([^/]+)", clean)
        if match:
            return match.group(1), match.group(2).replace(".git", "")
    elif "/" in clean and len(clean.split("/")) == 2:
        parts = clean.split("/")
        return parts[0], parts[1]
    raise ValueError(f"Invalid GitHub repository identifier: '{input_str}'. Expected 'owner/repo' or URL.")


def compute_sha256(url: str, client: httpx.Client) -> str:
    """Download stream and calculate SHA256 without loading entire file in memory."""
    hasher = hashlib.sha256()
    with client.stream("GET", url, follow_redirects=True) as resp:
        if resp.status_code != 200:
            raise ValueError(f"Failed to download archive from {url} (HTTP {resp.status_code})")
        for chunk in resp.iter_bytes(chunk_size=65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def scaffold_from_github(input_str: str, version: Optional[str] = None) -> Recipe:
    """Inspect GitHub repository and generate an automated recipe."""
    owner, repo = parse_github_repo(input_str)
    headers = {"User-Agent": "capsid-scaffolder/1.0", "Accept": "application/vnd.github.v3+json"}

    with httpx.Client(timeout=20.0, headers=headers) as client:
        # 1. Fetch repo metadata
        repo_resp = client.get(f"https://api.github.com/repos/{owner}/{repo}")
        if repo_resp.status_code != 200:
            raise ValueError(f"Could not find GitHub repository '{owner}/{repo}' (HTTP {repo_resp.status_code})")
        repo_data = repo_resp.json()
        description = repo_data.get("description") or f"{repo} tool"
        homepage = repo_data.get("homepage") or repo_data.get("html_url")
        license_dict = repo_data.get("license") or {}
        spdx_license = license_dict.get("spdx_id") or "UNKNOWN"

        # 2. Fetch release or tag
        rel_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        rel_resp = client.get(rel_url)
        tag_name = None
        tarball_url = None
        sha256_hash = None
        release_assets = []

        if rel_resp.status_code == 200:
            rel_data = rel_resp.json()
            tag_name = rel_data.get("tag_name", "")
            release_assets = rel_data.get("assets", [])
            # Prefer official release tarball if attached in assets
            for asset in release_assets:
                name = asset.get("name", "")
                if name.endswith(".tar.bz2") or name.endswith(".tar.gz"):
                    tarball_url = asset.get("browser_download_url")
                    break
            if not tarball_url:
                tarball_url = rel_data.get("tarball_url")
        else:
            # Fallback to tags
            tags_resp = client.get(f"https://api.github.com/repos/{owner}/{repo}/tags")
            if tags_resp.status_code == 200 and tags_resp.json():
                tag_name = tags_resp.json()[0].get("name", "v1.0.0")
                tarball_url = f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag_name}.tar.gz"

        # Clean version string (remove leading 'v' if present)
        clean_version = tag_name.lstrip("v") if tag_name else "1.0.0"

        # Calculate sha256 if tarball_url found
        if tarball_url:
            try:
                sha256_hash = compute_sha256(tarball_url, client)
            except Exception:
                sha256_hash = None

        # 3. Detect build system by inspecting root files
        contents_resp = client.get(f"https://api.github.com/repos/{owner}/{repo}/contents")
        root_files = set()
        if contents_resp.status_code == 200:
            root_files = {item.get("name", "") for item in contents_resp.json()}

        # Heuristic detection of build type
        if "Cargo.toml" in root_files:
            build_type = BuildType.RUST_CARGO
            build_deps = ["build-essential", "curl", "libssl-dev", "pkg-config"]
            run_deps = []
            test_cmd = f"{repo.lower()} --version"
        elif "configure.ac" in root_files or "configure" in root_files:
            build_type = BuildType.AUTOTOOLS
            build_deps = ["build-essential", "ca-certificates", "curl", "zlib1g-dev", "libbz2-dev", "liblzma-dev", "libncurses5-dev"]
            run_deps = ["zlib1g", "libbz2-1.0", "liblzma5", "libncurses6"]
            test_cmd = f"{repo.lower()} --version"
        elif "CMakeLists.txt" in root_files:
            build_type = BuildType.CMAKE
            build_deps = ["build-essential", "cmake", "ca-certificates", "curl", "zlib1g-dev"]
            run_deps = ["zlib1g"]
            test_cmd = f"{repo.lower()} --version"
        elif "Makefile" in root_files:
            build_type = BuildType.MAKE
            build_deps = ["build-essential", "ca-certificates", "curl", "zlib1g-dev"]
            run_deps = ["zlib1g"]
            test_cmd = f"{repo.lower()} --version"
        else:
            # Fallback to Pixi / Conda or generic Autotools
            build_type = BuildType.AUTOTOOLS
            build_deps = ["build-essential", "ca-certificates", "curl"]
            run_deps = []
            test_cmd = f"{repo.lower()} --help"

    return Recipe(
        name=repo.lower(),
        version=clean_version,
        description=description,
        homepage=homepage,
        license=spdx_license,
        categories=["bioinformatics", "genomics"],
        platforms=[Platform.LINUX_AMD64, Platform.LINUX_ARM64],
        build=BuildConfig(
            type=build_type,
            source=tarball_url,
            sha256=sha256_hash,
            dependencies={"build": build_deps, "run": run_deps}
        ),
        test=TestConfig(
            command=test_cmd,
            expected_output=None
        )
    )
