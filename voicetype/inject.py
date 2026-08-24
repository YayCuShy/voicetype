"""Text injection: type into the focused window on Wayland/X11.

Fallback chain (first success wins):
  1. ydotool   - kernel uinput; works everywhere, needs writable /dev/uinput
  2. wtype     - Wayland virtual-keyboard protocol; fails on GNOME/Mutter
  3. clipboard - wl-copy + simulated Ctrl+V; last resort

The clipboard is saved before method 3 and restored after, so dictation
never destroys what the user had copied.
"""

import logging
import os
import shutil
import subprocess
import time

log = logging.getLogger(__name__)


def _wayland_env() -> dict:
    env = dict(os.environ)
    if not env.get("WAYLAND_DISPLAY"):
        run_dir = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        try:
            sockets = sorted(f for f in os.listdir(run_dir)
                             if f.startswith("wayland-"))
            if sockets:
                env["WAYLAND_DISPLAY"] = sockets[0]
                log.debug("auto-detected WAYLAND_DISPLAY=%s", sockets[0])
        except OSError:
            pass
    return env


def _clipboard_get(env: dict) -> str | None:
    try:
        r = subprocess.run(["wl-paste", "--no-newline"], env=env,
                           capture_output=True, text=True, timeout=2)
        return r.stdout if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _clipboard_set(env: dict, text: str) -> bool:
    try:
        r = subprocess.run(["wl-copy", "--", text], env=env,
                           capture_output=True, text=True, timeout=2)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _ydotool_type(text: str, delay_ms: int) -> bool:
    r = subprocess.run(
        ["ydotool", "type", "--delay", str(delay_ms), "--", text],
        capture_output=True, text=True, timeout=30 + len(text) * delay_ms // 100)
    if r.returncode != 0:
        log.warning("ydotool failed rc=%d: %s", r.returncode, r.stderr.strip())
    return r.returncode == 0


def _wtype_type(text: str, env: dict) -> bool:
    r = subprocess.run(["wtype", text], env=env, capture_output=True,
                       text=True, timeout=10)
    if r.returncode != 0:
        log.warning("wtype failed rc=%d: %s", r.returncode, r.stderr.strip())
    return r.returncode == 0


def _clipboard_paste(text: str, env: dict) -> bool:
    old = _clipboard_get(env)
    if not _clipboard_set(env, text):
        return False
    time.sleep(0.05)
    # ydotool key codes: 29=left-ctrl, 47=v ; press both, release both
    r = subprocess.run(
        ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
        capture_output=True, text=True, timeout=5)
    ok = r.returncode == 0
    if not ok:
        log.warning("clipboard paste failed: %s", r.stderr.strip())
    else:
        time.sleep(0.3)                      # let the target app consume it
        if old is not None:
            _clipboard_set(env, old)         # restore user's clipboard
    return ok


def inject_text(text: str, delay_ms: int = 8) -> bool:
    """Type `text` at the current cursor. Returns True if any method worked."""
    if not text.strip():
        log.debug("inject_text: nothing to type")
        return False
    if shutil.which("ydotool") is None and shutil.which("wtype") is None \
            and shutil.which("wl-copy") is None:
        log.error("no injection tool found (install ydotool)")
        return False

    env = _wayland_env()

    if shutil.which("ydotool"):
        if _ydotool_type(text, delay_ms):
            return True
    if shutil.which("wtype"):
        if _wtype_type(text, env):
            return True
    if shutil.which("wl-copy") and shutil.which("ydotool"):
        if _clipboard_paste(text, env):
            return True

    log.error("all injection methods failed")
    return False
