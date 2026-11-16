"""
Golden tests: rendered Dockerfiles / workflow exports / catalog for every hub recipe and stack
must match tests/golden/expected/ byte for byte. Regenerate deliberately with
`python scripts/update_golden.py` and review the diff.
"""
import difflib
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "update_golden", Path(__file__).resolve().parent.parent / "scripts/update_golden.py")
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)

RENDERED = golden.render_all()
EXPECTED = {str(p.relative_to(golden.EXPECTED)): p for p in golden.EXPECTED.rglob("*") if p.is_file()}


def test_golden_set_is_complete():
    assert sorted(RENDERED) == sorted(EXPECTED), "run scripts/update_golden.py"
    assert any(k.startswith("stacks/") for k in RENDERED)
    assert any(k.endswith("/Dockerfile") and k.startswith("recipes/") for k in RENDERED)


@pytest.mark.parametrize("rel", sorted(EXPECTED))
def test_golden_file(rel):
    expected = EXPECTED[rel].read_text()
    got = RENDERED.get(rel)
    if got != expected:
        diff = "".join(difflib.unified_diff(
            expected.splitlines(True), (got or "").splitlines(True),
            fromfile=f"expected/{rel}", tofile="rendered", n=2))
        pytest.fail(f"{rel} differs from golden output:\n{diff[:4000]}")
