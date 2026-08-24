"""Injection fallback chain: order, success short-circuit, clipboard safety.

All subprocess calls are mocked; we verify orchestration, not the tools.
"""

import subprocess
from unittest.mock import patch

import pytest

from voicetype import inject


def _which_factory(available: dict[str, bool]):
    return lambda name: name if available.get(name) else None


@pytest.fixture
def no_clipboard(monkeypatch):
    monkeypatch.setattr(inject, "_clipboard_get", lambda env: None)
    monkeypatch.setattr(inject, "_clipboard_set", lambda env, t: True)


def test_empty_text_is_noop(no_clipboard):
    assert inject.inject_text("   ") is False


def test_no_tools_at_all_fails(no_clipboard, monkeypatch):
    monkeypatch.setattr(inject.shutil, "which",
                        _which_factory({}))
    assert inject.inject_text("hello") is False


def test_ydotool_success_short_circuits(no_clipboard, monkeypatch):
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wtype": True, "wl-copy": True}))
    called = []
    with patch.object(inject.subprocess, "run",
                      side_effect=lambda *a, **k:
                      called.append(a[0][0]) or subprocess.CompletedProcess([], 0)):
        assert inject.inject_text("hello") is True
    assert called == ["ydotool"]          # never tried wtype/clipboard


def test_falls_through_to_wtype_when_ydotool_missing(no_clipboard, monkeypatch):
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"wtype": True}))
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0)) as run:
        assert inject.inject_text("hi") is True
        assert run.call_args.args[0][0] == "wtype"


def test_clipboard_saved_and_restored_on_paste_path(monkeypatch):
    """When only the clipboard method can work, original content survives."""
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wl-copy": True}))
    # make ydotool type fail (rc=1) but ydotool key (paste) succeed;
    # distinguish via argv contents
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["ydotool", "type"]:
            fail = subprocess.CompletedProcess([], 1)
            fail.stderr = "boom"
            return fail
        if cmd[:2] == ["ydotool", "key"]:
            return subprocess.CompletedProcess([], 0)
        return subprocess.CompletedProcess([], 0)
    with patch.object(inject.subprocess, "run", side_effect=fake_run), \
         patch.object(inject, "_clipboard_get", return_value="USER DATA"), \
         patch.object(inject, "_clipboard_set") as setter:
        assert inject.inject_text("hi") is True
    restored_with = [c.args[1] for c in setter.call_args_list]
    assert "hi" in restored_with          # our text went in
    assert "USER DATA" in restored_with   # ...and user's clipboard came back


def test_wayland_display_autodetected(monkeypatch, tmp_path):
    socket_name = "wayland-3"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    (tmp_path / socket_name).touch()
    monkeypatch.setattr(inject.shutil, "which", _which_factory({"wtype": True}))

    captured = {}
    def fake_run(cmd, env=None, **kw):
        captured["env"] = env
        return subprocess.CompletedProcess([], 0)
    with patch.object(inject.subprocess, "run", side_effect=fake_run):
        assert inject.inject_text("x") is True
    assert captured["env"]["WAYLAND_DISPLAY"] == socket_name
