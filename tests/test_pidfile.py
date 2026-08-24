"""PID file lifecycle: claim, read, stale detection, identity defense."""

import os

import pytest

from voicetype import pidfile


@pytest.fixture
def pid_path(tmp_path, monkeypatch):
    path = tmp_path / "voicetype.pid"
    monkeypatch.setattr(pidfile, "pid_file", lambda: path)
    return path


def test_claim_creates_file_and_second_claim_loses(pid_path, monkeypatch):
    """O_EXCL race logic: existing valid daemon file must block new claims.

    Identity checking is a separate concern (tested above); here we fake
    'this pid is a real daemon' so only the race behaviour is exercised.
    """
    import subprocess
    proc = subprocess.Popen(["sleep", "30"])
    try:
        monkeypatch.setattr(pidfile, "_is_voicetype", lambda pid: True)
        pid_path.write_text(f"{proc.pid} tag\n")
        assert pidfile.claim_pid_file("tag") is None
    finally:
        proc.kill()


def test_read_missing_returns_none(pid_path):
    assert pidfile.read_pid() is None


def test_read_corrupt_removes_file(pid_path):
    pid_path.write_text("garbage!!")
    assert pidfile.read_pid() is None
    assert not pid_path.exists()


def test_read_stale_dead_pid_removes_file(pid_path):
    # find a pid that doesn't exist: scan up from an unlikely base
    dead = next(p for p in range(4_000_000, 4_100_000)
                if not os.path.exists(f"/proc/{p}"))
    pid_path.write_text(f"{dead} tag\n")
    assert pidfile.read_pid() is None
    assert not pid_path.exists()


def test_identity_defense_rejects_non_voicetype_process(pid_path):
    """A live PID whose cmdline lacks '-m voicetype' must be rejected.

    We spawn `sleep 30` - alive, real, but obviously not our daemon.
    (Using the pytest process itself would false-pass, since this repo's
    directory name puts 'voicetype' in pytest's own cmdline!)
    """
    import subprocess
    proc = subprocess.Popen(["sleep", "30"])
    try:
        pid_path.write_text(f"{proc.pid} tag\n")
        assert pidfile.read_pid() is None
        assert not pid_path.exists()
    finally:
        proc.kill()


def test_identity_accepts_console_script_launch(pid_path):
    """Regression: pip console scripts have argv[0]=python (kernel rewrites
    shebangs), with the script path in argv[1]. Reproduce that exact shape
    with [python, voicetype.py, daemon]."""
    import subprocess
    import sys
    fake_bin = pid_path.parent / "voicetype.py"
    fake_bin.write_text("import time; time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, str(fake_bin), "daemon"])
    try:
        pid_path.write_text(f"{proc.pid} tag\n")
        assert pidfile.read_pid() == proc.pid
    finally:
        proc.kill()


def test_release_only_deletes_own_file(pid_path):
    other_pid = 1                      # init: alive, definitely not ours
    pid_path.write_text(f"{other_pid} tag\n")
    pidfile.release_pid_file()
    assert pid_path.exists()           # foreign pid file untouched

    pidfile.claim_pid_file("tag")
    pidfile.release_pid_file()
    assert not pid_path.exists()       # our own file cleaned up


def test_release_missing_file_is_noop(tmp_path):
    pidfile.release_pid_file(tmp_path / "nope.pid")   # must not raise
