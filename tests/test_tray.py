"""Tray UI: state table, icon generation, graceful degradation."""

import pytest

from voicetype import tray


def test_states_table_complete():
    for state in ("idle", "recording", "transcribing"):
        assert state in tray.STATES
        color, label = tray.STATES[state]
        assert color.startswith("#") and len(color) == 7 and label


def test_circle_pixels_are_valid_rgba():
    px = tray.circle_pixels("#ff0000", size=24)
    assert len(px) == 24 * 24 * 4
    # center pixel fully opaque red
    center = (12 * 24 + 12) * 4
    assert px[center:center + 4] == b"\xff\x00\x00\xff"
    # corner pixel fully transparent (straight alpha: color may remain)
    assert px[0:4] == b"\xff\x00\x00\x00"


def test_set_state_ignores_unknown_and_repeated():
    ui = tray.TrayUI.__new__(tray.TrayUI)   # no gi needed: skip __init__
    ui.state = "idle"
    ui._glib = None
    ui.set_state("bogus")                   # unknown: ignored, no crash
    ui.set_state("idle")                    # same as current: ignored
    assert ui.state == "idle"
    assert not hasattr(ui, "_pending")      # never scheduled an update


def test_create_tray_disabled_returns_none():
    assert tray.create_tray(enabled=False) is None


def test_create_tray_degrades_gracefully_when_gi_missing(monkeypatch):
    """Headless boxes / minimal installs must not crash the daemon."""
    import sys
    monkeypatch.setitem(sys.modules, "gi", None)   # `import gi` -> ImportError
    assert tray.create_tray(enabled=True) is None
