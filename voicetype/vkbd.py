"""Persistent virtual keyboard on /dev/uinput (pure stdlib, no deps).

Why this exists: without ydotoold, every `ydotool` invocation creates a
fresh virtual input device, sends a few events and DESTROYS the device.
GNOME/Mutter tracks modifier state per device, so a Ctrl+Shift+V chord
split across three short-lived ydotool processes lands as orphan key
presses - the paste silently fails and the app later pastes stale
clipboard content instead.

This module creates ONE virtual keyboard at daemon start and keeps it
alive for the process lifetime. Modifier chords pressed and released on
this single device are indistinguishable from a real keyboard.
"""

import fcntl
import logging
import os
import struct
import time

log = logging.getLogger(__name__)

# linux/uinput.h ioctl numbers (64-bit safe: size encoded in request)
_UI_SET_EVBIT = 0x40045564
_UI_SET_KEYBIT = 0x40045565
_UI_DEV_SETUP = 0x405C5503
_UI_DEV_CREATE = 0x5501
_UI_DEV_DESTROY = 0x5502

_EV_SYN = 0x00
_EV_KEY = 0x01
_BUS_USB = 0x03

# struct input_event { timeval(16) __u16 type __u16 code __s32 value }
_EVENT = struct.Struct("=qqHHi")

_UINPUT_DEVICE = "/dev/uinput"


class VirtualKeyboard:
    """One persistent uinput keyboard able to press the given key codes."""

    def __init__(self, key_codes: list[int], name: str = "voicetype-vkbd"):
        self._codes = sorted(set(key_codes))
        self.fd = os.open(_UINPUT_DEVICE, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, _UI_SET_EVBIT, _EV_SYN)
            fcntl.ioctl(self.fd, _UI_SET_EVBIT, _EV_KEY)
            for code in self._codes:
                fcntl.ioctl(self.fd, _UI_SET_KEYBIT, code)
            # struct uinput_setup { input_id(HHHH) name[80] ff_effects_max(I) }
            setup = struct.pack("=HHHH80sI", _BUS_USB, 0x1D52, 0x1234,
                                0x0100, name.encode(), 0)
            fcntl.ioctl(self.fd, _UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, _UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        # give udev / the compositor a moment to enumerate the device so
        # the FIRST real chord is not raced away
        time.sleep(0.25)
        log.info("virtual keyboard created (%d key codes, id=%s)",
                 len(self._codes), name)

    def _emit(self, etype: int, code: int, value: int) -> None:
        os.write(self.fd, _EVENT.pack(0, 0, etype, code, value))

    def press(self, code: int) -> None:
        self._emit(_EV_KEY, code, 1)
        self._emit(_EV_SYN, 0, 0)

    def release(self, code: int) -> None:
        self._emit(_EV_KEY, code, 0)
        self._emit(_EV_SYN, 0, 0)

    def close(self) -> None:
        if getattr(self, "fd", None) is None:
            return
        try:
            fcntl.ioctl(self.fd, _UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            log.exception("failed to destroy virtual keyboard")
        finally:
            self.fd = None
            log.info("virtual keyboard destroyed")
