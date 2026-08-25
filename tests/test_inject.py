"""Injection fallback chain: order, success short-circuit, clipboard safety.

All subprocess calls are mocked and the virtual keyboard is replaced by a
recording fake; we verify orchestration, not the kernel interface.
"""

import subprocess
from unittest.mock import patch

import pytest

from voicetype import inject


def _which_factory(available: dict[str, bool]):
    return lambda name: name if available.get(name) else None


class FakeKb:
    """Records press/release order instead of touching /dev/uinput."""

    def __init__(self):
        self.events = []

    def press(self, code):
        self.events.append((code, 1))

    def release(self, code):
        self.events.append((code, 0))

    def close(self):
        pass


@pytest.fixture
def fake_kb(monkeypatch):
    kb = FakeKb()
    monkeypatch.setattr(inject, "_shared_keyboard", None)
    monkeypatch.setattr(inject, "_keyboard_dead", False)
    monkeypatch.setattr(inject, "get_keyboard", lambda: kb)
    yield kb


@pytest.fixture
def no_keyboard(monkeypatch):
    monkeypatch.setattr(inject, "_shared_keyboard", None)
    monkeypatch.setattr(inject, "_keyboard_dead", True)
    monkeypatch.setattr(inject, "get_keyboard", lambda: None)


@pytest.fixture
def no_clipboard(monkeypatch):
    monkeypatch.setattr(inject, "_clipboard_get", lambda env: None)
    monkeypatch.setattr(inject, "_clipboard_set", lambda env, t: True)


def test_empty_text_is_noop(no_clipboard, fake_kb):
    assert inject.inject_text("   ") is False


def test_no_tools_at_all_fails(no_clipboard, no_keyboard, monkeypatch):
    monkeypatch.setattr(inject.shutil, "which",
                        _which_factory({}))
    assert inject.inject_text("hello") is False


def test_wl_copy_stdio_detached(fake_kb):
    """Regression: wl-copy's forked clipboard-server child inherits our
    pipes, so capture_output blocks until timeout and paste silently
    fails. stdio must be DEVNULL."""
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0)) as run:
        assert inject._clipboard_set({}, "x") is True
    kw = run.call_args.kwargs
    assert kw["stdout"] == subprocess.DEVNULL
    assert kw["stderr"] == subprocess.DEVNULL
    assert kw["stdin"] == subprocess.DEVNULL


def test_paste_is_primary_short_circuits_typing(fake_kb, monkeypatch):
    """Clipboard-paste must run before keystroke synthesis and succeed,
    so ydotool/wtype are never reached."""
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wtype": True, "wl-copy": True}))
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          [], 0, stdout="hello")) as run:
        assert inject.inject_text("hello") is True
    tools = [c.args[0][0] for c in run.call_args_list]
    # read old -> copy -> verify ownership -> restore old (chord is vkbd)
    assert tools[0] == "wl-paste" and tools[1] == "wl-copy"
    assert tools[-1] == "wl-copy"                       # clipboard restored
    assert all(t != "wtype" for t in tools)             # never reached
    assert all(c.args[0][1] != "type" for c in run.call_args_list)


def test_paste_waits_for_clipboard_ownership(no_clipboard, fake_kb,
                                             monkeypatch):
    """Regression: pasting before wl-copy claims ownership delivered the
    user's STALE clipboard. First read must see old content and retry."""
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wl-copy": True}))
    responses = iter([subprocess.CompletedProcess([], 0, stdout=None),
                      subprocess.CompletedProcess([], 0, stdout="OLD CLIP"),
                      subprocess.CompletedProcess([], 0, stdout="NEW TEXT")])
    def fake_run(cmd, **kw):
        if cmd[0] == "wl-paste":
            return next(responses)
        return subprocess.CompletedProcess([], 0)
    with patch.object(inject.subprocess, "run", side_effect=fake_run) as run:
        assert inject.inject_text("NEW TEXT") is True
    cmds = [c.args[0][0] for c in run.call_args_list]
    assert cmds.count("wl-paste") >= 2                       # retried once


def test_paste_is_layout_safe_for_apostrophes(no_clipboard, fake_kb,
                                              monkeypatch):
    """Regression: US-keycode typing mangles 'you're' into dead-accent
    artifacts on Spanish layouts - text must travel through the clipboard
    untouched."""
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wtype": True, "wl-copy": True}))
    copied = []
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0)) as run, \
         patch.object(inject, "_clipboard_get", return_value=None), \
         patch.object(inject, "_clipboard_set",
                      side_effect=lambda env, t: copied.append(t) or True):
        assert inject.inject_text("it's you're") is True
    assert copied[0] == "it's you're"        # verbatim, not retyped


def test_falls_through_to_wtype_when_ydotool_missing(no_clipboard,
                                                     no_keyboard,
                                                     monkeypatch):
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"wtype": True}))
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0)) as run:
        assert inject.inject_text("hi") is True
        assert run.call_args.args[0][0] == "wtype"


def test_chord_uses_single_persistent_device(no_clipboard, fake_kb,
                                             monkeypatch):
    """The whole combo must come from ONE long-lived device with proper
    ordering - fragmented chords across throwaway ydotool processes lose
    modifier state on GNOME/Mutter and silently fail to paste."""
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wl-copy": True}))
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          [], 0, stdout="x")) as run, \
         patch.object(inject, "_clipboard_get", return_value=None), \
         patch.object(inject, "_clipboard_set", return_value=True):
        assert inject.inject_text("hi", paste_binding="ctrl+shift+v") is True
    key_calls = [c.args[0] for c in run.call_args_list
                 if c.args[0][:2] == ["ydotool", "key"]]
    assert key_calls == []                    # never spawn throwaway devices
    # ctrl+shift held, then v tapped, then modifiers released in reverse
    assert fake_kb.events == [
        (29, 1), (42, 1),                     # mods down
        (47, 1), (47, 0),                     # v tap
        (42, 0), (29, 0),                     # mods up in reverse
    ]


def test_ydotool_chord_only_without_uinput(no_clipboard, no_keyboard,
                                           monkeypatch):
    """Without /dev/uinput access the legacy phased ydotool path runs."""
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wl-copy": True}))
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          [], 0, stdout="x")) as run, \
         patch.object(inject, "_clipboard_get", return_value=None), \
         patch.object(inject, "_clipboard_set", return_value=True):
        assert inject.inject_text("hi", paste_binding="ctrl+v") is True
    key_calls = [c.args[0] for c in run.call_args_list
                 if c.args[0][:2] == ["ydotool", "key"]]
    assert key_calls == [
        ["ydotool", "key", "29:1"],           # mods down
        ["ydotool", "key", "47:1"],           # v down
        ["ydotool", "key", "47:0"],           # v up
        ["ydotool", "key", "29:0"],           # mods up
    ]


def test_invalid_paste_binding_falls_back_to_typing(no_clipboard, fake_kb,
                                                    monkeypatch):
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wl-copy": True}))
    with patch.object(inject.subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          [], 0, stdout="")) as run:
        assert inject.inject_text("hi", paste_binding="ctrl+bogus") is True
    assert run.call_args.args[0][:2] == ["ydotool", "type"]


def test_clipboard_saved_and_restored_on_paste_path(no_clipboard, fake_kb,
                                                    monkeypatch):
    """When only the clipboard method can work, original content survives."""
    monkeypatch.setattr(inject.shutil, "which", _which_factory(
        {"ydotool": True, "wl-copy": True}))

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["ydotool", "type"]:
            fail = subprocess.CompletedProcess([], 1)
            fail.stderr = "boom"
            return fail
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
