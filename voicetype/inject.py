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
    """wl-copy forks a child that serves the clipboard; if that child
    inherits our stdout/stderr pipes, subprocess.run blocks until timeout
    (the fork keeps them open) and we silently fall back to keystrokes.
    Detach all stdio so the parent's exit closes the pipe immediately."""
    try:
        r = subprocess.run(
            ["wl-copy", "--", text], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=3)
        if r.returncode != 0:
            log.warning("wl-copy exited rc=%d", r.returncode)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("wl-copy failed: %s", e)
        return False


def _ydotool_type(text: str, delay_ms: int) -> bool:
    r = subprocess.run(
        ["ydotool", "type", "--delay", str(delay_ms), "--", text],
        capture_output=True, text=True, timeout=30 + len(text) * delay_ms // 100)
    if r.returncode != 0:
        log.warning("ydotool failed rc=%d: %s", r.returncode, r.stderr.strip())
    else:
        log.info("delivered via ydotool typing (layout-sensitive!)")
    return r.returncode == 0


def _wtype_type(text: str, env: dict) -> bool:
    r = subprocess.run(["wtype", text], env=env, capture_output=True,
                       text=True, timeout=10)
    if r.returncode != 0:
        log.warning("wtype failed rc=%d: %s", r.returncode, r.stderr.strip())
    else:
        log.info("delivered via wtype")
    return r.returncode == 0


def _press_keys(env: dict, events: list[str]) -> bool:
    r = subprocess.run(["ydotool", "key", *events],
                       capture_output=True, text=True, timeout=5)
    return r.returncode == 0


def _clipboard_paste(text: str, env: dict, binding: str = "ctrl+v") -> bool:
    old = _clipboard_get(env)
    if not _clipboard_set(env, text):
        return False
    # Race guard: wl-copy takes a moment to claim ownership. Pasting before
    # it does delivers the user's OLD clipboard (stale-text bug). Poll until
    # the clipboard verifiably holds our payload; proceed optimistically on
    # read failure so desktops without wl-paste still work.
    deadline = time.time() + 0.4
    verified = False
    while time.time() < deadline:
        try:
            r = subprocess.run(["wl-paste", "--no-newline"], env=env,
                               capture_output=True, text=True, timeout=1)
            if r.returncode != 0:
                verified = True            # can't verify: optimistic
                break
            if r.stdout == text:
                verified = True
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            verified = True                # can't verify: optimistic
            break
        time.sleep(0.03)
    if not verified:
        log.warning("clipboard never matched payload - pasting anyway")
        verified = True                    # old behaviour beats no output

    # Send the chord in PHASES with real gaps between them: firing all
    # key-down/up events back-to-back gets terminals to miss the modifier
    # state entirely (combo silently does nothing).
    keys = [k.strip().lower() for k in binding.split("+") if k.strip()]
    unknown = [k for k in keys if k not in _KEY_CODES]
    if unknown:
        log.warning("unknown keys %s in paste_binding - falling back "
                    "to keystroke typing", unknown)
        return False
    mods = [_KEY_CODES[k] for k in keys if k != "v" and k != "c"]
    main = _KEY_CODES[keys[-1]]
    ok = True
    ok &= _press_keys(env, [f"{m}:1" for m in mods])          # mods down
    time.sleep(0.05)
    ok &= _press_keys(env, [f"{main}:1"])                     # main down
    time.sleep(0.05)
    ok &= _press_keys(env, [f"{main}:0"])                     # main up
    time.sleep(0.05)
    ok &= _press_keys(env, [f"{m}:0" for m in reversed(mods)])  # mods up
    time.sleep(0.05)                                          # settle
    if not ok:
        log.warning("paste combo failed: %s", binding)
        return False
    log.info("delivered via clipboard paste (%s)", binding)
    time.sleep(0.3)                      # let the target app consume it
    if old is not None:
        _clipboard_set(env, old)         # restore user's clipboard
    return True


# key codes for paste-combo synthesis (linux/input-event-codes.h)
_KEY_CODES = {"ctrl": 29, "shift": 42, "alt": 56, "v": 47, "c": 46, "x": 45}


def inject_text(text: str, delay_ms: int = 8,
                paste_binding: str = "ctrl+v") -> bool:
    """Deliver `text` to the focused window. True if any method worked.

    Order matters: clipboard-paste is layout/Unicode-safe (keystroke
    synthesis reinterprets keycodes through the user's xkb layout, which
    mangles apostrophes etc. on non-US layouts), so it goes first.
    """
    if not text.strip():
        log.debug("inject_text: nothing to type")
        return False
    if shutil.which("ydotool") is None and shutil.which("wtype") is None \
            and shutil.which("wl-copy") is None:
        log.error("no injection tool found (install ydotool)")
        return False

    env = _wayland_env()
    time.sleep(0.05)          # let focus settle after recording stops

    # 1. clipboard paste - Unicode & layout safe, clipboard restored after
    if shutil.which("wl-copy") and shutil.which("ydotool"):
        if _clipboard_paste(text, env, paste_binding):
            return True
    # 2. direct keystroke synthesis (ASCII-safe only)
    if shutil.which("ydotool"):
        if _ydotool_type(text, delay_ms):
            return True
    if shutil.which("wtype"):
        if _wtype_type(text, env):
            return True

    log.error("all injection methods failed")
    return False
