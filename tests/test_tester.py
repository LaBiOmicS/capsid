import hashlib
from pathlib import Path

from capsid.models import TestConfig
from capsid.tester import _prepare_workdir, build_test_command, check_result


def test_plain_test_runs_without_workdir_or_env():
    t = TestConfig(command="tool --version")
    assert _prepare_workdir(t) is None
    cmd = build_test_command("docker", t, "img:1", None, None)
    assert cmd == ["docker", "run", "--rm", "img:1", "bash", "-c", "tool --version"]


def test_files_are_written_and_mounted_at_test(tmp_path):
    t = TestConfig(
        command="wc -l in/a.txt > out.txt", files={"in/a.txt": "x\ny\n"}, output_file="out.txt", random_seed=7
    )
    wd = _prepare_workdir(t)
    try:
        assert (wd / "in/a.txt").read_text() == "x\ny\n"
        assert oct(wd.stat().st_mode)[-3:] == "777"
        cmd = build_test_command("podman", t, "img:1", None, wd)
        assert cmd[:3] == ["podman", "run", "--rm"]
        assert cmd[3:5] == ["-e", "CAPSID_RANDOM_SEED=7"] and cmd[5:7] == ["-e", "PYTHONHASHSEED=7"]
        assert ["-v", f"{wd}:/test", "-w", "/test"] == cmd[7:11]
        app = build_test_command("apptainer", t, "img:1", Path("t.sif"), wd)
        assert ["--bind", f"{wd}:/test", "--pwd", "/test"] == app[6:10] and "t.sif" in app
    finally:
        import shutil

        shutil.rmtree(wd)


def test_check_result_exit_code_output_and_checksum(tmp_path):
    data = b"2 in/a.txt\n"
    good = hashlib.sha256(data).hexdigest()
    t = TestConfig(command="x", expected_output="ok", output_file="out.txt", expected_checksum=good)
    assert "exit code" in check_result(t, 1, "ok", tmp_path)
    assert "not found in output" in check_result(t, 0, "nope", tmp_path)
    assert "was not created" in check_result(t, 0, "ok", tmp_path)
    (tmp_path / "out.txt").write_bytes(data)
    assert check_result(t, 0, "ok", tmp_path) is None
    (tmp_path / "out.txt").write_bytes(b"other")
    assert "Checksum" in check_result(t, 0, "ok", tmp_path)


def test_timeout_default_and_validation():
    import pytest
    from pydantic import ValidationError

    assert TestConfig(command="x").timeout == 60
    with pytest.raises(ValidationError):
        TestConfig(command="x", timeout=0)


def test_apptainer_without_sif_uses_the_image():
    from capsid.models import TestConfig
    from capsid.tester import apptainer_source, build_test_command

    assert (
        apptainer_source("ghcr.io/labiomics/capsid/samtools:1.25") == "docker://ghcr.io/labiomics/capsid/samtools:1.25"
    )
    assert apptainer_source("localhost:5000/x:1") == "docker://localhost:5000/x:1"
    assert apptainer_source("samtools:1.25") == "docker-daemon:samtools:1.25"
    cmd = build_test_command("apptainer", TestConfig(command="samtools --help"), "samtools:1.25", None, None)
    assert cmd[2] == "docker-daemon:samtools:1.25"
