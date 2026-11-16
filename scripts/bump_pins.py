#!/usr/bin/env python3
"""
Refresh the digest-pinned images and the apt snapshot in capsid/builders/dockerfile_gen.py.

Every "<image>:<tag>@sha256:<digest>" literal is re-resolved for the SAME tag (version bumps
stay a deliberate change) and DEFAULT_APT_SNAPSHOT is set to today's date (UTC), which is
always newer than the refreshed base images.

    python scripts/bump_pins.py            # rewrite the file, print what changed
    python scripts/bump_pins.py --check    # exit 1 if anything is outdated, change nothing
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "src/capsid/builders/dockerfile_gen.py"
PIN_RE = re.compile(r'"(?P<ref>[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9._-]+)@(?P<digest>sha256:[0-9a-f]{64})"')
SNAPSHOT_RE = re.compile(r'^DEFAULT_APT_SNAPSHOT = "(?P<ts>\d{8}T\d{6}Z)"$', re.M)


def resolve_digest(ref: str) -> str:
    """Index (multi-arch manifest list) digest of image:tag, as the registry serves it."""
    out = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest.Digest}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    digest = json.loads(out)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError(f"Unexpected digest for {ref}: {digest!r}")
    return digest


def bump(text: str, resolve: Callable[[str], str], snapshot: str) -> tuple[str, list[str]]:
    """Return (new_text, human-readable changes)."""
    changes: list[str] = []

    def repl(m: "re.Match[str]") -> str:
        new = resolve(m["ref"])
        if new != m["digest"]:
            changes.append(f"{m['ref']}: {m['digest'][:19]} -> {new[:19]}")
        return f'"{m["ref"]}@{new}"'

    text = PIN_RE.sub(repl, text)
    m = SNAPSHOT_RE.search(text)
    if not m:
        raise RuntimeError("DEFAULT_APT_SNAPSHOT not found")
    if m["ts"] != snapshot:
        changes.append(f"DEFAULT_APT_SNAPSHOT: {m['ts']} -> {snapshot}")
        text = SNAPSHOT_RE.sub(f'DEFAULT_APT_SNAPSHOT = "{snapshot}"', text)
    return text, changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only report; exit 1 if outdated")
    ap.add_argument("--snapshot", help="apt snapshot timestamp (default: today 00:00 UTC)")
    args = ap.parse_args()

    snapshot = args.snapshot or dt.datetime.now(dt.UTC).strftime("%Y%m%dT000000Z")
    old = TARGET.read_text()
    new, changes = bump(old, resolve_digest, snapshot)
    for c in changes:
        print(c)
    if not changes:
        print("All pins are current.")
        return 0
    if args.check:
        return 1
    TARGET.write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
