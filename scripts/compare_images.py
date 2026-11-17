#!/usr/bin/env python3
"""
Measure container images for the same tool versions from different sources.

For each image reference the script records, for linux/amd64:
  - the resolved digest, the platforms the reference offers and the number of layers
  - compressed size (sum of layer blobs in the manifest) and uncompressed size (`docker image inspect`)
  - the default user and the base operating system
  - whether a Cosign signature (`sha256-<digest>.sig`) or attestation (`.att`) exists in the same repository
  - the packages Trivy recognises (by ecosystem) and the vulnerabilities it reports, by severity, with the
    subset that has a fix available, and whether an operating-system package database was present

Usage:
  python3 scripts/compare_images.py benchmark.yaml --out results.json

benchmark.yaml lists the comparisons:
  sources: {capsid: "...", biocontainers: "...", staphb: "..."}   # human-readable source names
  tools:
    - tool: samtools
      version: "1.25"
      images:
        capsid: ghcr.io/labiomics/capsid/samtools:1.25
        biocontainers: quay.io/biocontainers/samtools:1.25--h9dcdb79_0

Requires Docker (with buildx). Trivy runs from a pinned container image against the local Docker daemon,
so each image is pulled once; images are removed after measurement unless --keep is given.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

TRIVY_IMAGE = "aquasec/trivy:0.75.0"
PLATFORM = "linux/amd64"
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def raw_manifest(ref: str) -> dict[str, Any] | None:
    r = run(["docker", "buildx", "imagetools", "inspect", "--raw", ref], check=False)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def digest_of(ref: str) -> str:
    r = run(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"])
    return json.loads(r.stdout)["digest"]


def repo_of(ref: str) -> str:
    """Repository part of a reference (no tag, no digest)."""
    name = ref.split("@", 1)[0]
    head, _, tail = name.rpartition("/")
    return f"{head}/{tail.split(':', 1)[0]}" if head else name.split(":", 1)[0]


def platforms_and_amd64(ref: str, index: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Platforms offered by the reference and the linux/amd64 image manifest."""
    if "manifests" in index:
        plats, amd64 = [], None
        for m in index["manifests"]:
            p = m.get("platform") or {}
            if p.get("os") in (None, "unknown"):
                continue  # attestation manifests
            plats.append(f"{p['os']}/{p['architecture']}" + (f"/{p['variant']}" if p.get("variant") else ""))
            if p.get("os") == "linux" and p.get("architecture") == "amd64":
                amd64 = raw_manifest(f"{repo_of(ref)}@{m['digest']}")
        if amd64 is None:
            raise RuntimeError(f"{ref}: no linux/amd64 manifest")
        return sorted(plats), amd64
    # single-platform manifest: read the platform from its config
    cfg = raw_manifest_blob_config(ref)
    return [f"{cfg.get('os')}/{cfg.get('architecture')}"], index


def raw_manifest_blob_config(ref: str) -> dict[str, Any]:
    r = run(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Image}}"])
    return json.loads(r.stdout)


def has_tag(repo: str, tag: str) -> bool:
    return raw_manifest(f"{repo}:{tag}") is not None


def trivy(ref: str, cache: Path) -> dict[str, Any]:
    cmd = [
        "docker", "run", "--rm",
        "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
        "-v", f"{cache}:/root/.cache/trivy",
        TRIVY_IMAGE, "image", "--quiet", "--image-src", "docker",
        "--scanners", "vuln", "--list-all-pkgs", "--format", "json", ref,
    ]  # fmt: skip
    return json.loads(run(cmd).stdout)


def summarise_trivy(report: dict[str, Any]) -> dict[str, Any]:
    pkgs: set[tuple[str, str, str]] = set()
    pkg_types: Counter[str] = Counter()
    vulns: dict[str, tuple[str, bool, str]] = {}
    for res in report.get("Results") or []:
        rtype = res.get("Type", "")
        for p in res.get("Packages") or []:
            key = (rtype, p.get("Name", ""), p.get("Version", ""))
            if key not in pkgs:
                pkgs.add(key)
                pkg_types[rtype] += 1
        for v in res.get("Vulnerabilities") or []:
            fixable = v.get("Status") == "fixed" or bool(v.get("FixedVersion"))
            vulns[v["VulnerabilityID"] + "|" + v.get("PkgName", "")] = (v.get("Severity", "UNKNOWN"), fixable, rtype)
    sev = Counter(s for s, _, _ in vulns.values())
    sev_fix = Counter(s for s, f, _ in vulns.values() if f)
    os_meta = (report.get("Metadata") or {}).get("OS") or {}
    os_types = {"debian", "ubuntu", "alpine", "redhat", "centos", "rocky", "alma", "amazon", "wolfi", "chainguard"}
    return {
        "os": f"{os_meta.get('Family', 'unknown')} {os_meta.get('Name', '')}".strip(),
        "os_eosl": bool(os_meta.get("EOSL")),
        "os_package_db": any(t in os_types for t in pkg_types),
        "packages": len(pkgs),
        "packages_by_type": dict(sorted(pkg_types.items())),
        "vulnerabilities": {s: sev.get(s, 0) for s in SEVERITIES},
        "vulnerabilities_fixable": {s: sev_fix.get(s, 0) for s in SEVERITIES},
        "vulnerabilities_by_type": dict(sorted(Counter(t for _, _, t in vulns.values()).items())),
    }


def measure(ref: str, cache: Path, keep: bool) -> dict[str, Any]:
    index = raw_manifest(ref)
    if index is None:
        raise RuntimeError(f"{ref}: not found")
    digest = digest_of(ref)
    platforms, amd64 = platforms_and_amd64(ref, index)
    compressed = sum(layer.get("size", 0) for layer in amd64.get("layers", [])) + amd64.get("config", {}).get("size", 0)
    repo = repo_of(ref)
    sig_tag = digest.replace(":", "-")

    run(["docker", "pull", "-q", "--platform", PLATFORM, ref])
    info = json.loads(run(["docker", "image", "inspect", ref]).stdout)[0]
    user = (info.get("Config") or {}).get("User") or ""
    try:
        scan = summarise_trivy(trivy(ref, cache))
    finally:
        if not keep:
            run(["docker", "rmi", "-f", ref], check=False)
    return {
        "ref": ref,
        "digest": digest,
        "platforms": platforms,
        "layers": len(amd64.get("layers", [])),
        "compressed_bytes": compressed,
        "uncompressed_bytes": info.get("Size"),
        "user": user or "root (unset)",
        "runs_as_root": user in ("", "0", "root", "0:0", "root:root"),
        "cosign_signature": has_tag(repo, f"{sig_tag}.sig"),
        "cosign_attestation": has_tag(repo, f"{sig_tag}.att"),
        **scan,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache", type=Path, default=Path(".trivy-cache"))
    ap.add_argument("--keep", action="store_true", help="keep pulled images")
    args = ap.parse_args()

    spec = yaml.safe_load(args.spec.read_text())
    args.cache.mkdir(parents=True, exist_ok=True)
    results = []
    for entry in spec["tools"]:
        row: dict[str, Any] = {"tool": entry["tool"], "version": str(entry["version"]), "images": {}}
        for source, ref in entry["images"].items():
            print(f"· {entry['tool']} {entry['version']} [{source}] {ref}", file=sys.stderr, flush=True)
            try:
                row["images"][source] = measure(ref, args.cache.resolve(), args.keep)
            except (RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
                row["images"][source] = {"ref": ref, "error": str(e).strip()[:300]}
        results.append(row)

    trivy_version = run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{args.cache.resolve()}:/root/.cache/trivy",
            TRIVY_IMAGE,
            "--version",
            "--format",
            "json",
        ]
    ).stdout
    out = {
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": PLATFORM,
        "scanner": {"image": TRIVY_IMAGE, "version": json.loads(trivy_version) if trivy_version.strip() else None},
        "sources": spec.get("sources", {}),
        "results": results,
    }
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
