"""
Dockerfile generator: multi-stage, hardened Dockerfiles for every build strategy.

Every image shares one runtime stage (see RUNTIME_TEMPLATE): non-root user bio:1000,
tini as PID 1, universal HPC mount points, and FAIR provenance assets in /etc/capsid/.

Reproducibility guarantees:
  * base and builder images are pinned by digest;
  * apt resolves against a fixed snapshot.debian.org timestamp (default images only);
  * conda/PyPI closures come from committed lockfiles (pixi.lock / requirements.lock with
    hashes) when present -- `capsid lock` generates them; without a lock the solver runs at
    build time and the resolved set is still recorded in /etc/capsid/;
  * source archives are verified against their sha256 before building.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import yaml
from jinja2 import Environment, StrictUndefined

from capsid.models import BuildType, Recipe
from capsid.sbom import generate_spdx_sbom
from capsid.ro_crate import generate_ro_crate
from capsid.exporter import export_nextflow_process, export_snakemake_rule
from capsid.locking import pixi_manifest

# Images pinned by digest (tag kept for readability). Update with:
#   python scripts/bump_pins.py   (or: docker buildx imagetools inspect <image>:<tag>)
# Dockerfile frontend (the `# syntax=` parser image) -- also part of the build input
DOCKERFILE_FRONTEND = (
    "docker/dockerfile:1.4@sha256:9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc"
)
DEFAULT_DEBIAN_RUNTIME = (
    "debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc"
)
DEFAULT_PIXI_BUILDER = (
    "ghcr.io/prefix-dev/pixi:0.84.2@sha256:8201f134655a8261f382ee565ddf1498e5f215887bb14e812e1f47c09f4f7196"
)
DEFAULT_UV_BUILDER = (
    "ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85"
)
# Official Python images used as builder AND runtime for uv-pypi recipes, so the venv's
# interpreter (/usr/local/bin/python3.X) exists in both stages.
PYTHON_RUNTIME_IMAGES = {
    "3.10": "python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370",
    "3.11": "python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39",
    "3.12": "python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df",
    "3.13": "python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72",
}
DEFAULT_PYTHON_VERSION = "3.12"

# snapshot.debian.org timestamp for apt. Must be newer than the base images above
# (bump together with the digests to pick up security updates).
DEFAULT_APT_SNAPSHOT = "20261115T000000Z"
DEBIAN_SUITE = "bookworm"
DEFAULT_SOURCE_DATE_EPOCH = 1700000000

VENV_PREFIX = "/opt/venv"
CONDA_PREFIX = "/opt/conda-env"

# Base PATH used in /etc/environment, which pam_env reads literally (no $PATH expansion).
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _dq(value: Any) -> str:
    """Escape a value for use inside a double-quoted Dockerfile string."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _shq(value: Any) -> str:
    """Quote a value as a double-quoted shell word (escapes \\ " $ `)."""
    escaped = str(value)
    for ch in ("\\", '"', "$", "`"):
        escaped = escaped.replace(ch, "\\" + ch)
    return f'"{escaped}"'


_ENV = Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    undefined=StrictUndefined,
    autoescape=False,
)
_ENV.filters["dq"] = _dq
_ENV.filters["shq"] = _shq



APT_SNAPSHOT_TEMPLATE = """\
# Freeze apt to snapshot.debian.org/{{ snapshot }} for reproducible system packages
RUN printf '%s\\n' \\
        'Types: deb' \\
        'URIs: http://snapshot.debian.org/archive/debian/{{ snapshot }}' \\
        'Suites: {{ suite }} {{ suite }}-updates' \\
        'Components: main' \\
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \\
        'Check-Valid-Until: no' \\
        '' \\
        'Types: deb' \\
        'URIs: http://snapshot.debian.org/archive/debian-security/{{ snapshot }}' \\
        'Suites: {{ suite }}-security' \\
        'Components: main' \\
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \\
        'Check-Valid-Until: no' \\
        > /etc/apt/sources.list.d/debian.sources \\
    && rm -f /etc/apt/sources.list \\
    && echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-capsid-retries
"""

UV_BUILDER_TEMPLATE = """\
FROM {{ uv_builder }} AS uv_bin

# ---- Builder: same image as runtime, so the venv's interpreter exists in both stages ----
FROM {{ builder_base }} AS python_builder
COPY --from=uv_bin /uv /uvx /bin/

ARG SOURCE_DATE_EPOCH={{ source_date_epoch }}
ENV UV_PYTHON_PREFERENCE=only-system \\
    UV_PYTHON_DOWNLOADS=never \\
    UV_COMPILE_BYTECODE=1 \\
    UV_LINK_MODE=copy

{{ apt_setup }}
RUN apt-get update && apt-get install -y --no-install-recommends \\
        ca-certificates \\
{% for dep in build_deps %}
        {{ dep }} \\
{% endfor %}
    && rm -rf /var/lib/apt/lists/* /var/log/apt/* /var/log/dpkg.log \\
        /var/cache/ldconfig/aux-cache /var/cache/debconf/*-old /var/lib/dpkg/*-old

RUN uv venv --python {{ python_version | shq }} {{ venv_prefix }}
ENV VIRTUAL_ENV={{ venv_prefix }} \\
    PATH="{{ venv_prefix }}/bin:$PATH"

{% if requirements_lock %}
# Locked closure with hashes (generated by `capsid lock`)
COPY <<'EOF_REQUIREMENTS_LOCK' /build/requirements.lock
{{ requirements_lock | trim }}
EOF_REQUIREMENTS_LOCK

RUN uv pip install --no-cache --require-hashes -r /build/requirements.lock
{% else %}
{% for pkg in pypi_packages %}
RUN uv pip install --no-cache {{ pkg | shq }}
{% endfor %}
{% endif %}

# Record the fully resolved dependency set for provenance and rebuilds
RUN uv pip freeze > /requirements.lock
"""


PIXI_BUILDER_TEMPLATE = """\
# ---- Builder: Pixi (Rust / rattler) conda environment ----
FROM {{ pixi_builder }} AS pixi_builder

WORKDIR /build
ARG SOURCE_DATE_EPOCH={{ source_date_epoch }}

{% if pixi_lock %}
# Locked environment (generated by `capsid lock`); manifest rendered by Capsid
COPY <<'EOF_PIXI_TOML' /build/pixi.toml
{{ pixi_manifest | trim }}
EOF_PIXI_TOML

COPY <<'EOF_PIXI_LOCK' /build/pixi.lock
{{ pixi_lock | trim }}
EOF_PIXI_LOCK

RUN pixi install --locked
{% else %}
RUN pixi init{% for ch in channels %} --channel {{ ch | shq }}{% endfor %} /build
RUN pixi add {{ conda_packages | map('shq') | join(' ') }}
{% endif %}
"""


SOURCE_BUILDER_TEMPLATE = """\
# ---- Builder: compile from verified source ----
FROM {{ builder_base }} AS source_builder
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG SOURCE_DATE_EPOCH={{ source_date_epoch }}

{{ apt_setup }}
RUN apt-get update && apt-get install -y --no-install-recommends \\
        build-essential \\
        ca-certificates \\
        curl \\
        bzip2 \\
        gzip \\
        xz-utils \\
        tar \\
{% for dep in build_deps %}
        {{ dep }} \\
{% endfor %}
    && rm -rf /var/lib/apt/lists/* /var/log/apt/* /var/log/dpkg.log \\
        /var/cache/ldconfig/aux-cache /var/cache/debconf/*-old /var/lib/dpkg/*-old

WORKDIR /src
RUN curl -fsSL {{ source | shq }} -o archive.tar \\
    && echo "{{ sha256 }}  archive.tar" | sha256sum -c - \\
    && mkdir -p /src/unpack \\
    && tar -xf archive.tar -C /src/unpack --strip-components=1

WORKDIR /src/unpack

# Embed an RPATH so binaries find their own libraries without a global LD_LIBRARY_PATH
ENV LDFLAGS="-Wl,-rpath,{{ prefix }}/lib"

{% if build_commands %}
{% for cmd in build_commands %}
RUN {{ cmd }}
{% endfor %}
{% else %}
RUN if [ -f configure ]; then \\
        ./configure --prefix={{ prefix }}{% for a in configure_args %} {{ a }}{% endfor %}; \\
    fi && \\
    make -j"$(nproc)" && \\
    (make install prefix={{ prefix }} || make install)
{% endif %}

# Strip debug symbols to reduce image size
RUN if [ -d {{ prefix }}/bin ]; then \\
        strip {{ prefix }}/bin/* 2>/dev/null || true; \\
    fi
"""


RUNTIME_TEMPLATE = """\
# ---- Runtime: hardened, non-root, tini as PID 1, HPC & workflow-engine ready ----
FROM {{ runtime_base }} AS runtime

ARG SOURCE_DATE_EPOCH={{ source_date_epoch }}

{{ apt_setup }}
RUN apt-get update && apt-get install -y --no-install-recommends \\
        tini \\
        ca-certificates \\
        bash \\
{% for dep in run_deps %}
        {{ dep }} \\
{% endfor %}
    && rm -rf /var/lib/apt/lists/* /var/log/apt/* /var/log/dpkg.log \\
        /var/cache/ldconfig/aux-cache /var/cache/debconf/*-old /var/lib/dpkg/*-old

# Non-privileged user, universal HPC mount points, sticky scratch permissions
RUN groupadd -r -g 1000 bio && \\
    useradd -r -u 1000 -g bio -m -d /home/bio -s /bin/bash bio && \\
    mkdir -p /scratch /data /work /lustre /shared /tmp /home/bio && \\
    chmod 1777 /tmp /scratch /data /work /lustre /shared

# PATH for login shells (profile.d) and PAM sessions (/etc/environment is not expanded)
RUN echo 'export PATH="{{ path }}:${PATH:-}"' > /etc/profile.d/capsid.sh && \\
    chmod 0644 /etc/profile.d/capsid.sh && \\
    echo 'PATH="{{ path }}:{{ system_path }}"' >> /etc/environment

# Tool prefixes are owned by root: readable and executable by bio, not writable
{% for c in copies %}
COPY --from={{ c[0] }} {{ c[1] }} {{ c[2] }}
{% endfor %}
RUN chmod -R a+rX {{ copies | map(attribute=2) | join(' ') }}

# -------------------------------------------------------------
# FAIR & provenance assets (/etc/capsid/)
# -------------------------------------------------------------
RUN mkdir -p /etc/capsid
{% for c in provenance_copies %}
COPY --from={{ c[0] }} {{ c[1] }} {{ c[2] }}
{% endfor %}
{% for asset in fair_assets %}

COPY <<'{{ asset.marker }}' /etc/capsid/{{ asset.filename }}
{{ asset.content }}
{{ asset.marker }}
{% endfor %}

RUN chmod -R a+rX /etc/capsid

ENV PATH="{{ path }}:$PATH" \\
{% for k, v in extra_env %}
    {{ k }}="{{ v | dq }}" \\
{% endfor %}
    HOME="/home/bio" \\
    LC_ALL=C.UTF-8 \\
    LANG=C.UTF-8 \\
    XDG_CACHE_HOME="/tmp/.cache" \\
    PYTHONPYCACHEPREFIX="/tmp/pycache" \\
    NUMBA_CACHE_DIR="/tmp/numba" \\
    MPLCONFIGDIR="/tmp/matplotlib"

LABEL {% for k, v in labels %}{% if not loop.first %}      {% endif %}{{ k }}="{{ v | dq }}"{% if not loop.last %} \\{% endif %}

{% endfor %}

# Numeric UID so Kubernetes runAsNonRoot can verify it
USER 1000:1000
WORKDIR /home/bio

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash"]
"""


def python_image(version: str) -> str:
    """Digest-pinned python:X-slim-bookworm image for a Python version."""
    try:
        return PYTHON_RUNTIME_IMAGES[version]
    except KeyError:
        raise ValueError(
            f"Unsupported python_version '{version}'. Supported: {sorted(PYTHON_RUNTIME_IMAGES)} "
            "(or set build.base_image to an image that provides it)"
        ) from None


def render(template: str, **context: Any) -> str:
    return _ENV.from_string(template).render(**context)


def apt_setup(snapshot: Optional[str], base_overridden: bool) -> str:
    """apt snapshot snippet; empty for custom base images or snapshot='latest'."""
    if base_overridden or snapshot == "latest":
        return ""
    return render(APT_SNAPSHOT_TEMPLATE, snapshot=snapshot or DEFAULT_APT_SNAPSHOT, suite=DEBIAN_SUITE).rstrip()


def render_dockerfile(
    *,
    header: str,
    builders: List[str],
    runtime_base: str,
    source_date_epoch: int,
    run_deps: List[str],
    path_dirs: List[str],
    copies: List[Tuple[str, str, str]],
    provenance_copies: List[Tuple[str, str, str]],
    labels: List[Tuple[str, str]],
    apt: str,
    fair_assets: Optional[List[Dict[str, str]]] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> str:
    """Assemble builder stage(s) + the shared hardened runtime stage."""
    runtime = render(
        RUNTIME_TEMPLATE,
        runtime_base=runtime_base,
        source_date_epoch=source_date_epoch,
        apt_setup=apt,
        run_deps=run_deps,
        path=":".join(path_dirs),
        system_path=SYSTEM_PATH,
        copies=copies,
        provenance_copies=provenance_copies,
        fair_assets=fair_assets or [],
        extra_env=sorted((extra_env or {}).items()),
        labels=labels,
    )
    parts = [f"# syntax={DOCKERFILE_FRONTEND}\n" + header.rstrip() + "\n"]
    parts.extend(b.rstrip() + "\n" for b in builders)
    parts.append(runtime)
    return "\n".join(parts)


def _fair_assets(recipe: Recipe) -> List[Dict[str, str]]:
    recipe_yaml = yaml.dump(recipe.model_dump(mode="json", exclude_none=True), sort_keys=False).strip()
    return [
        {"marker": "EOF_RECIPE", "filename": "recipe.yaml", "content": recipe_yaml},
        {"marker": "EOF_SBOM", "filename": "sbom.spdx.json",
         "content": json.dumps(generate_spdx_sbom(recipe), indent=2)},
        {"marker": "EOF_ROCRATE", "filename": "ro-crate-metadata.json",
         "content": json.dumps(generate_ro_crate(recipe), indent=2)},
        {"marker": "EOF_NF", "filename": "module.nf", "content": export_nextflow_process(recipe).strip()},
        {"marker": "EOF_SMK", "filename": "rule.smk", "content": export_snakemake_rule(recipe).strip()},
    ]


def _recipe_labels(recipe: Recipe) -> List[Tuple[str, str]]:
    labels: List[Tuple[str, str]] = [
        ("org.opencontainers.image.title", recipe.name),
        ("org.opencontainers.image.version", recipe.version),
        ("org.opencontainers.image.description", recipe.description or ""),
        ("org.opencontainers.image.url", recipe.homepage or ""),
        ("org.opencontainers.image.licenses", recipe.license or "UNKNOWN"),
        ("org.opencontainers.image.vendor", "Capsid Project"),
    ]
    if recipe.doi:
        labels.append(("org.opencontainers.image.doi", recipe.doi))
    labels += [
        ("io.capsid.version", "0.1.0"),
        ("io.capsid.engine", recipe.build.type.value),
        ("io.capsid.fair.ro_crate", "/etc/capsid/ro-crate-metadata.json"),
        ("io.capsid.fair.sbom", "/etc/capsid/sbom.spdx.json"),
        ("io.capsid.fair.module_nf", "/etc/capsid/module.nf"),
        ("io.capsid.fair.rule_smk", "/etc/capsid/rule.smk"),
    ]
    if recipe.edam_topics:
        labels.append(("io.capsid.fair.edam_topics", ",".join(recipe.edam_topics)))
    if recipe.edam_operations:
        labels.append(("io.capsid.fair.edam_operations", ",".join(recipe.edam_operations)))
    labels += [
        ("io.capsid.hpc", "apptainer,singularity,slurm"),
        ("io.capsid.orchestrators", "nextflow,snakemake"),
    ]
    for k, v in recipe.labels.items():
        labels.append((k, v))
    return labels


def generate_dockerfile(recipe: Recipe) -> str:
    """Render the optimized, hardened, FAIR Dockerfile for a single recipe."""
    b = recipe.build
    python_version = b.python_version or DEFAULT_PYTHON_VERSION
    if b.base_image:
        runtime_base = b.base_image
    elif b.type == BuildType.UV_PYPI:
        runtime_base = python_image(python_version)
    else:
        runtime_base = DEFAULT_DEBIAN_RUNTIME
    sde = b.source_date_epoch or DEFAULT_SOURCE_DATE_EPOCH
    apt = apt_setup(b.apt_snapshot, base_overridden=bool(b.base_image))
    build_deps = list(b.dependencies.get("build", []))
    run_deps = list(b.dependencies.get("run", []))

    if b.type == BuildType.UV_PYPI:
        if not b.package:
            raise ValueError(f"Recipe '{recipe.name}': uv-pypi builds require 'build.package'")
        header = "# Multi-stage hardened Python container generated by Capsid (uv)"
        builders = [render(
            UV_BUILDER_TEMPLATE,
            uv_builder=DEFAULT_UV_BUILDER,
            builder_base=runtime_base,
            source_date_epoch=sde,
            apt_setup=apt,
            venv_prefix=VENV_PREFIX,
            python_version=python_version,
            build_deps=build_deps,
            pypi_packages=[b.package],
            requirements_lock=recipe.requirements_lock,
        )]
        path_dirs = [f"{VENV_PREFIX}/bin"]
        copies = [("python_builder", VENV_PREFIX, VENV_PREFIX)]
        provenance = [("python_builder", "/requirements.lock", "/etc/capsid/requirements.lock")]

    elif b.type == BuildType.PIXI_CONDA:
        if not b.conda_packages:
            raise ValueError(f"Recipe '{recipe.name}': pixi-conda builds require 'build.conda_packages'")
        header = "# Multi-stage hardened Conda container generated by Capsid via Pixi (Rust)"
        channels = b.channels or ["conda-forge", "bioconda"]
        builders = [render(
            PIXI_BUILDER_TEMPLATE,
            pixi_builder=DEFAULT_PIXI_BUILDER,
            source_date_epoch=sde,
            channels=channels,
            conda_packages=b.conda_packages,
            pixi_lock=recipe.pixi_lock,
            pixi_manifest=pixi_manifest(recipe.name, channels, b.conda_packages, recipe.platforms),
        )]
        path_dirs = [f"{CONDA_PREFIX}/bin"]
        copies = [("pixi_builder", "/build/.pixi/envs/default", CONDA_PREFIX)]
        provenance = [("pixi_builder", "/build/pixi.lock", "/etc/capsid/pixi.lock")]

    else:
        if not b.source:
            raise ValueError(f"Recipe '{recipe.name}': {b.type.value} builds require 'build.source'")
        if not b.sha256:
            raise ValueError(
                f"Recipe '{recipe.name}': refusing to build unverified source '{b.source}'. "
                "Add 'build.sha256'."
            )
        prefix = b.install_prefix or f"/opt/{recipe.name}"
        header = "# Multi-stage compilation container generated by Capsid"
        builders = [render(
            SOURCE_BUILDER_TEMPLATE,
            builder_base=runtime_base,
            source_date_epoch=sde,
            apt_setup=apt,
            build_deps=build_deps,
            source=b.source,
            sha256=b.sha256,
            prefix=prefix,
            build_commands=b.build_commands or [],
            configure_args=b.configure_args or [],
        )]
        path_dirs = [f"{prefix}/bin"]
        copies = [("source_builder", prefix, prefix)]
        provenance = []

    return render_dockerfile(
        header=header,
        builders=builders,
        runtime_base=runtime_base,
        source_date_epoch=sde,
        run_deps=run_deps,
        path_dirs=path_dirs,
        copies=copies,
        provenance_copies=provenance,
        labels=_recipe_labels(recipe),
        apt=apt,
        fair_assets=_fair_assets(recipe),
        extra_env=b.env,
    )
