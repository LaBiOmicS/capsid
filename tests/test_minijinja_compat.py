"""
The templates are meant to be shared with a future Rust/Wasm core (minijinja).
Render the whole golden set through minijinja (Rust engine, Python bindings) and require
byte-identical output.
"""
import importlib.util
from pathlib import Path

import pytest

minijinja = pytest.importorskip("minijinja")

import capsid.builders.dockerfile_gen as dockerfile_gen  # noqa: E402
import capsid.builders.stack_builder as stack_builder  # noqa: E402
import capsid.exporter as exporter  # noqa: E402
from capsid import templating  # noqa: E402

TEMPLATES = Path(templating.__file__).parent / "templates"


def _minijinja_render():
    env = minijinja.Environment(
        loader=lambda name: (TEMPLATES / name).read_text() if (TEMPLATES / name).is_file() else None,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined_behavior="strict",
        auto_escape_callback=lambda name: False,
    )
    env.add_filter("dq", templating.dq)
    env.add_filter("shq", templating.shq)
    return lambda name, **ctx: env.render_template(name, **ctx)


def test_golden_set_renders_identically_with_minijinja(monkeypatch):
    render = _minijinja_render()
    monkeypatch.setattr(dockerfile_gen, "render", render)
    monkeypatch.setattr(exporter, "render", render)
    monkeypatch.setattr(stack_builder, "render", render)
    calls = []
    for mod in (dockerfile_gen, exporter, stack_builder):
        monkeypatch.setattr(mod, "render", lambda name, _r=render, **ctx: calls.append(name) or _r(name, **ctx))
    spec = importlib.util.spec_from_file_location(
        "update_golden", Path(__file__).resolve().parent.parent / "scripts/update_golden.py")
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)

    rendered = golden.render_all()
    differing = [k for k, v in rendered.items() if (golden.EXPECTED / k).read_text() != v]
    assert not differing, f"minijinja output differs for: {differing}"
    assert {"dockerfile/runtime.j2", "dockerfile/pixi_builder.j2", "dockerfile/uv_builder.j2",
            "exports/nextflow_config.j2", "exports/apptainer.def.j2"} <= set(calls)
