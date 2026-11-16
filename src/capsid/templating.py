"""
Template environment shared by every generator.

Templates live in capsid/templates/*.j2 and are rendered with trim_blocks + lstrip_blocks +
keep_trailing_newline, strict undefined (a missing variable is an error, never an empty
string) and no autoescape. Two custom filters are available:

  dq   escape for a double-quoted Dockerfile string  (\\ -> \\\\, " -> \\", newline -> space)
  shq      quote as a double-quoted shell word        (escapes \\ " $ `)
  heredoc  refuse content containing its heredoc terminator line
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


def heredoc(content: Any, marker: str) -> str:
    """
    Content of a quoted heredoc (COPY <<'MARKER'). A line equal to the marker would end the
    heredoc early and turn the rest of the content into Dockerfile instructions, so it is refused.
    """
    text = str(content)
    if any(line.strip() == marker for line in text.splitlines()):
        raise ValueError(f"Embedded content contains a line equal to its heredoc marker '{marker}'")
    return text


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
ENV.filters["heredoc"] = heredoc


def render(name: str, **context: Any) -> str:
    """Render templates/<name> (e.g. 'dockerfile/runtime.j2')."""
    return ENV.get_template(name).render(**context)
