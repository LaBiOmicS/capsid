import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "bump_pins", Path(__file__).resolve().parent.parent / "scripts/bump_pins.py")
bump_pins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_pins)

OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
SRC = f'''X = (
    "ghcr.io/prefix-dev/pixi:0.84.2@{OLD}"
)
Y = {{"3.12": "python:3.12-slim-bookworm@{OLD}"}}
DEFAULT_APT_SNAPSHOT = "20261115T000000Z"
'''


def test_bump_keeps_tags_and_updates_digests_and_snapshot():
    seen = []

    def resolve(ref):
        seen.append(ref)
        return NEW

    text, changes = bump_pins.bump(SRC, resolve, "20261120T000000Z")
    assert seen == ["ghcr.io/prefix-dev/pixi:0.84.2", "python:3.12-slim-bookworm"]
    assert f"pixi:0.84.2@{NEW}" in text and f"slim-bookworm@{NEW}" in text
    assert OLD not in text
    assert 'DEFAULT_APT_SNAPSHOT = "20261120T000000Z"' in text
    assert len(changes) == 3


def test_bump_is_noop_when_current():
    text, changes = bump_pins.bump(SRC, lambda ref: OLD, "20261115T000000Z")
    assert text == SRC and changes == []


def test_real_source_has_pins_and_snapshot():
    src = bump_pins.TARGET.read_text()
    refs = [m["ref"] for m in bump_pins.PIN_RE.finditer(src)]
    assert "docker/dockerfile:1.4" in refs
    assert len(refs) >= 8
    assert bump_pins.SNAPSHOT_RE.search(src)


def test_missing_snapshot_is_an_error():
    with pytest.raises(RuntimeError):
        bump_pins.bump("nothing here", lambda r: NEW, "20261120T000000Z")
