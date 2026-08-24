"""System tray icon showing daemon state at a glance.

    grey dot   = idle (ready)
    red dot    = recording (mic live)
    orange dot = transcribing

Implemented as an Ayatana AppIndicator over DBus - the only tray mechanism
GNOME still supports. Icons are generated at runtime with GdkPixbuf, so the
package ships no image assets and needs no Pillow.

The indicator must be touched from the GLib main thread; set_state() is
thread-safe and marshals updates there via GLib.idle_add.
"""

import logging
import os
import signal
import threading

log = logging.getLogger(__name__)

STATES = {
    "idle": ("#9e9e9e", "ready"),
    "recording": ("#e53935", "recording - mic live"),
    "transcribing": ("#fb8c00", "transcribing..."),
}
ICON_SIZE = 24


def _hex_to_rgba(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def circle_pixels(hex_color: str, size: int = ICON_SIZE) -> bytes:
    """RGBA bytes for an anti-aliased filled circle (for GdkPixbuf)."""
    r, g, b = _hex_to_rgba(hex_color)
    center = (size - 1) / 2
    radius = size / 2 - 1.5
    buf = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            d = ((x - center) ** 2 + (y - center) ** 2) ** 0.5
            alpha = max(0.0, min(1.0, radius - d + 0.5))   # 1px soft edge
            i = (y * size + x) * 4
            buf[i:i + 4] = bytes((r, g, b, int(alpha * 255)))
    return bytes(buf)


class TrayUI:
    def __init__(self):
        import gi
        gi.require_version("AyatanaAppIndicator3", "0.1")
        gi.require_version("Gtk", "3.0")
        from gi.repository import AyatanaAppIndicator3, GLib, Gtk

        self._glib = GLib
        self._icon_dir = os.path.join(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
            "voicetype")
        os.makedirs(self._icon_dir, exist_ok=True)
        self._paths = {s: self._write_icon(s) for s in STATES}
        self.state = "idle"

        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "voicetype", "voicetype",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(
            AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label=f"Status: {STATES['idle'][1]}")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        toggle = Gtk.MenuItem(label="Toggle recording")
        toggle.connect("activate",
                       lambda *_: os.kill(os.getpid(), signal.SIGUSR1))
        menu.append(toggle)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate",
                          lambda *_: os.kill(os.getpid(), signal.SIGTERM))
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)
        self.indicator.set_icon_full(self._paths["idle"], "voicetype idle")

        self._loop = GLib.MainLoop()
        threading.Thread(target=self._loop.run, daemon=True,
                         name="voicetype-tray").start()
        log.info("tray icon active")

    # ── thread-safe state updates ────────────────────────────────────────────
    def set_state(self, state: str) -> None:
        if state == self.state or state not in STATES:
            return
        self.state = state
        self._glib.idle_add(self._apply, state)

    def _apply(self, state: str) -> bool:
        color_hint, label = STATES[state]
        try:
            self.indicator.set_icon_full(self._paths[state],
                                         f"voicetype {state}")
            self.status_item.set_label(f"Status: {label}")
            self.indicator.set_title(f"voicetype - {label}")
        except Exception:
            log.exception("tray update failed")
        return False    # remove this idle callback

    # ── icon assets ──────────────────────────────────────────────────────────
    def _write_icon(self, state: str) -> str:
        from gi.repository import GdkPixbuf
        hex_color = STATES[state][0]
        size = ICON_SIZE
        pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            data=circle_pixels(hex_color, size),
            colorspace=GdkPixbuf.Colorspace.RGB,
            has_alpha=True,
            bits_per_sample=8,
            width=size, height=size,
            rowstride=size * 4,
        )
        path = os.path.join(self._icon_dir, f"{state}.png")
        pixbuf.savev(path, "png", [], [])
        return path

    def stop(self) -> None:
        self._glib.idle_add(self._loop.quit)


def create_tray(enabled: bool = True):
    """Build a TrayUI, or None if disabled/unavailable (never fatal)."""
    if not enabled:
        return None
    try:
        return TrayUI()
    except Exception as e:
        log.warning("tray unavailable (%s) - continuing headless", e)
        return None
