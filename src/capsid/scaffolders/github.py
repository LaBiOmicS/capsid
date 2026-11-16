"""
GitHub Scaffolder: inspects GitHub repositories and releases to generate build recipes.
"""

import hashlib
import re

import httpx

from capsid.models import BuildConfig, BuildType, Platform, Recipe, TestConfig

# Release asset names that denote a prebuilt binary rather than a source archive
BINARY_ASSET_RE = re.compile(
    r"(x86[_-]64|amd64|aarch64|arm64|armv7|i[3-6]86|linux|darwin|macos|osx|windows|win(32|64)|musl|gnu|apple)",
    re.IGNORECASE,
)


def parse_github_repo(input_str: str) -> tuple[str, str]:
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


def scaffold_from_github(input_str: str, version: str | None = None) -> Recipe:
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

        # 2. Fetch the requested release (or the latest one), falling back to tags
        tag_name = None
        tarball_url = None
        sha256_hash = None
        candidates = [f"releases/tags/{version}", f"releases/tags/v{version}"] if version else ["releases/latest"]
        rel_data = None
        for path in candidates:
            rel_resp = client.get(f"https://api.github.com/repos/{owner}/{repo}/{path}")
            if rel_resp.status_code == 200:
                rel_data = rel_resp.json()
                break

        if rel_data:
            tag_name = rel_data.get("tag_name", "")
            # Prefer an official *source* tarball attached as an asset (e.g. samtools-1.21.tar.bz2);
            # assets named after a platform are prebuilt binaries, not sources
            for asset in rel_data.get("assets", []):
                name = asset.get("name", "")
                if name.endswith((".tar.bz2", ".tar.gz", ".tar.xz")) and not BINARY_ASSET_RE.search(name):
                    tarball_url = asset.get("browser_download_url")
                    break
        else:
            tags_resp = client.get(f"https://api.github.com/repos/{owner}/{repo}/tags")
            tags = [t.get("name", "") for t in tags_resp.json()] if tags_resp.status_code == 200 else []
            if version:
                tag_name = next((t for t in tags if t in (version, f"v{version}")), None)
                if not tag_name:
                    raise ValueError(f"No release or tag '{version}' found in {owner}/{repo}")
            elif tags:
                tag_name = tags[0]
        if tag_name and not tarball_url:
            tarball_url = f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag_name}.tar.gz"

        clean_version = tag_name.lstrip("v") if tag_name else (version or "1.0.0")

        # Checksum of the exact archive the build will download (left empty if unreachable:
        # `capsid lint` then reports E011 and the build refuses to run)
        if tarball_url:
            try:
                sha256_hash = compute_sha256(tarball_url, client)
            except (ValueError, httpx.HTTPError):
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
            build_deps = [
                "build-essential",
                "ca-certificates",
                "curl",
                "zlib1g-dev",
                "libbz2-dev",
                "liblzma-dev",
                "libncurses5-dev",
            ]
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
            type=build_type, source=tarball_url, sha256=sha256_hash, dependencies={"build": build_deps, "run": run_deps}
        ),
        test=TestConfig(command=test_cmd, expected_output=None),
    )
