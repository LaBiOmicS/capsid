from pathlib import Path

import pytest

from capsid.builders.runner import publish_command


def test_docker_publish_is_multiarch_and_reproducible():
    [cmd] = publish_command(
        "docker", Path("ctx/Dockerfile"), Path("ctx"), "ghcr.io/o/tool:1.0", ["linux/amd64", "linux/arm64"], 1700000000
    )
    assert cmd[:3] == ["docker", "buildx", "build"]
    assert cmd[cmd.index("--platform") + 1] == "linux/amd64,linux/arm64"
    assert "SOURCE_DATE_EPOCH=1700000000" in cmd
    assert "--provenance=false" in cmd
    out = cmd[cmd.index("--output") + 1]
    assert out == "type=image,name=ghcr.io/o/tool:1.0,push=true,unpack=false,rewrite-timestamp=true"
    assert cmd[-1] == "ctx"


def test_podman_publish_builds_manifest_then_pushes_all():
    build, push = publish_command("podman", Path("ctx/Dockerfile"), Path("ctx"), "reg/t:1", ["linux/amd64"], 42)
    assert "--rewrite-timestamp" in build
    assert build[build.index("--source-date-epoch") + 1] == "42"
    assert build[build.index("--manifest") + 1] == "reg/t:1"
    assert push == ["podman", "manifest", "push", "--all", "reg/t:1", "docker://reg/t:1"]


def test_publish_rejects_unsupported_engine_and_empty_platforms():
    with pytest.raises(RuntimeError):
        publish_command("nerdctl", Path("D"), Path("."), "t", ["linux/amd64"], 1)
    with pytest.raises(RuntimeError):
        publish_command("docker", Path("D"), Path("."), "t", [], 1)
