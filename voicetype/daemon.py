"""Daemon: signal-driven state machine.

    IDLE --SIGUSR1--> RECORDING --SIGUSR1--> TRANSCRIBING --> IDLE

Signals do no work themselves; handlers only release a counting semaphore
(one permit per keypress) so toggles that arrive mid-transcription are
queued instead of lost. The main loop owns all state transitions - doing
real work inside Python signal handlers causes reentrancy bugs.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from .config import Config
from .engine import Engine
from .notify import notify
from .pidfile import claim_pid_file, read_pid, release_pid_file
from .recorder import Recorder

log = logging.getLogger(__name__)

TAG = "voicetype-daemon"
SESSIONS_DIR = Path.home() / ".local" / "share" / "voicetype" / "sessions"


def save_session(audio: np.ndarray, sample_rate: int, text: str) -> None:
    """Archive utterance audio + transcript so mismatches are debuggable."""
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        pcm = (np.clip(audio.flatten(), -1, 1) * 32767).astype("<i2")
        with wave.open(str(SESSIONS_DIR / f"{stamp}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm.tobytes())
        (SESSIONS_DIR / f"{stamp}.txt").write_text(text + "\n")
        log.debug("archived %s.wav", stamp)
    except OSError:
        log.exception("failed to archive session")


class Daemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.signals = threading.Semaphore(0)
        self.recorder = Recorder(cfg.sample_rate, cfg.mic_device,
                                 keep_mic_open=cfg.keep_mic_open)
        self.engine: Engine | None = None
        self.tray = None                  # set in run() if available
        self._pid_path: str | None = None
        self._busy = False

    def _ui(self, state: str) -> None:
        if self.tray is not None:
            self.tray.set_state(state)

    # ── signal handlers (must stay tiny) ─────────────────────────────────────
    def _on_toggle(self, _sig, _frame):
        self.signals.release()

    def _on_shutdown(self, _sig, _frame):
        self.recorder.close()
        release_pid_file(self._pid_path)
        sys.exit(0)

    # ── one dictation cycle ──────────────────────────────────────────────────
    def _cycle(self) -> None:
        self._ui("recording")
        notify("voicetype", "recording...", quiet=self.cfg.quiet)
        audio = self.recorder.record_until(self.signals.acquire,
                                           self.cfg.tail_padding_ms)
        dur = self.recorder.duration(audio, self.cfg.sample_rate)
        if dur < self.cfg.min_seconds:
            log.info("ignored %.2fs utterance (< %.1fs)",
                     dur, self.cfg.min_seconds)
            self._ui("idle")
            return

        assert self.engine is not None
        self._ui("transcribing")
        notify("voicetype", "transcribing...", quiet=self.cfg.quiet)
        text = self.engine.transcribe(audio, self.cfg.sample_rate,
                                      self.cfg.language or None)
        if self.cfg.save_audio:
            save_session(audio, self.cfg.sample_rate, text)
        if not text:
            return

        from .inject import inject_text          # deferred: keeps tests light
        if inject_text(text, self.cfg.ydotool_delay,
                       paste_binding=self.cfg.paste_binding):
            notify("voicetype", f'\u2705 "{text[:60]}"', quiet=self.cfg.quiet)
        else:
            notify("voicetype", f"injection failed: {text[:60]}",
                   quiet=self.cfg.quiet)
        self._ui("idle")

    def run(self) -> None:
        self._pid_path = str(claim_pid_file(TAG))
        if self._pid_path is None:
            sys.exit(f"daemon already running (pid file: {read_pid(pid_file())})")

        signal.signal(signal.SIGUSR1, self._on_toggle)
        signal.signal(signal.SIGTERM, self._on_shutdown)
        signal.signal(signal.SIGINT, self._on_shutdown)

        try:
            self.engine = Engine(self.cfg.model, self.cfg.device,
                                 self.cfg.compute_type)
        except Exception as e:
            release_pid_file(self._pid_path)
            sys.exit(f"failed to load model {self.cfg.model!r}: {e}")

        notify("voicetype", "ready", quiet=self.cfg.quiet)
        log.info("daemon ready (pid %d)", os.getpid())

        if self.cfg.tray:
            from .tray import create_tray
            self.tray = create_tray()
        if self.cfg.keep_mic_open:
            try:
                self.recorder.open()         # zero-delay recording start
            except Exception:
                log.exception("could not keep mic open; falling back "
                              "to per-utterance capture")
                self.cfg.keep_mic_open = False

        while True:
            self.signals.acquire()               # wait for first toggle
            if self._busy:
                continue                         # stays queued as a permit
            try:
                self._busy = True
                self._cycle()
            except Exception:
                log.exception("cycle failed")
                notify("voicetype", "error - see /tmp/voicetype.log",
                       quiet=self.cfg.quiet)
            finally:
                self._busy = False


# ── client operations ────────────────────────────────────────────────────────
def start_detached() -> int:
    """Spawn the daemon detached from any terminal/session."""
    logfile = open("/tmp/voicetype.log", "ab")
    return subprocess.Popen(
        [sys.executable, "-m", "voicetype"],
        stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
        start_new_session=True,
    ).pid


def client(mode: str, auto_start_timeout: float = 25.0) -> int:
    """toggle | stop | status. Returns process exit code."""
    if mode == "status":
        pid = read_pid()
        print(f"running (pid {pid})" if pid else "not running")
        return 0

    if mode == "stop":
        pid = read_pid()
        if pid is None:
            print("not running")
            return 0
        os.kill(pid, signal.SIGTERM)
        print(f"stopped pid {pid}")
        return 0

    # mode == "toggle"
    pid = read_pid()
    started_here = False
    if pid is None:
        print("daemon not running - starting it...")
        start_detached()
        deadline = time.time() + auto_start_timeout
        while (pid := read_pid()) is None:
            if time.time() > deadline:
                print("daemon failed to start; see /tmp/voicetype.log",
                      file=sys.stderr)
                return 1
            time.sleep(0.3)
        started_here = True
    os.kill(pid, signal.SIGUSR1)
    print(f"recording... (daemon pid {pid})" if started_here
          else "toggle sent")
    return 0
