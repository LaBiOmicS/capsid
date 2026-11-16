"""
Template environment shared by every generator.

Templates live in capsid/templates/*.j2 and are kept to the Jinja2 subset that minijinja
(Rust) also implements, so a future Rust/Wasm core can load the very same files:
trim_blocks + lstrip_blocks + keep_trailing_newline, strict undefined, no autoescape,
and only these custom filters (to be re-implemented there):

  dq   escape for a double-quoted Dockerfile string  (\\ -> \\\\, " -> \\", newline -> space)
  shq  quote as a double-quoted shell word           (escapes \\ " $ `)
"""
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined


def dq(value: Any) -> str:
    """Escape a value for use inside a double-quoted Dockerfile string."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def shq(value: Any) -> str:
    """Quote a value as a double-quoted shell word (escapes \\ " $ `)."""
    escaped = str(value)
    for ch in ("\\", '"', "$", "`"):
        escaped = escaped.replace(ch, "\\" + ch)
    return f'"{escaped}"'


ENV = Environment(
    loader=PackageLoader("capsid", "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    undefined=StrictUndefined,
    autoescape=False,
)
ENV.filters["dq"] = dq
ENV.filters["shq"] = shq


def render(name: str, **context: Any) -> str:
    """Render templates/<name> (e.g. 'dockerfile/runtime.j2')."""
    return ENV.get_template(name).render(**context)
