"""Microphone capture via sounddevice/PortAudio.

Two capture modes:
  - keep_mic_open=True: the InputStream is opened once and kept running;
    the callback only buffers while `capturing` is True. Recording starts
    instantly on hotkey - no first-word clipping while PortAudio warms up.
    Side effect: GNOME shows its mic-in-use dot permanently.
  - keep_mic_open=False: stream opens per utterance (privacy-friendly,
    but the first ~150ms of speech can be lost).
"""

import logging
import time
from collections.abc import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, sample_rate: int = 16000, device: int | None = None,
                 keep_mic_open: bool = True):
        self.sample_rate = sample_rate
        self.device = device
        self.keep_mic_open = keep_mic_open
        self._frames: list[np.ndarray] = []
        self._capturing = False
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            log.warning("audio overflow: %s", status)
        if self._capturing:
            self._frames.append(indata.copy())

    def _make_stream(self) -> sd.InputStream:
        return sd.InputStream(samplerate=self.sample_rate, channels=1,
                              dtype="float32", device=self.device,
                              callback=self._callback,
                              blocksize=0)   # let PortAudio choose

    # ── persistent mode ──────────────────────────────────────────────────────
    def open(self) -> None:
        """Open the stream now so recording later starts with zero delay."""
        if self._stream is not None:
            return
        self._stream = self._make_stream()
        self._stream.start()
        log.info("mic stream open (%d Hz, always-on)", self.sample_rate)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            log.info("mic stream closed")

    # ── capture ──────────────────────────────────────────────────────────────
    def record_until(self, wait_stop: Callable[[], None],
                     tail_padding_ms: int = 0) -> np.ndarray | None:
        """Capture mic audio until wait_stop() returns. Returns [n,1] float32.

        wait_stop is any blocking call that returns when recording should
        stop - e.g. threading.Event.wait or threading.Semaphore.acquire.

        tail_padding_ms: keep buffering this long after wait_stop() returns,
        so the hotkey pressed mid-word doesn't chop trailing consonants.
        """
        self._frames = []

        def _capture() -> None:
            self._capturing = True
            try:
                wait_stop()
                if tail_padding_ms > 0:
                    time.sleep(tail_padding_ms / 1000)
            finally:
                self._capturing = False

        if self._stream is not None:
            _capture()
        else:
            # lazy mode: open per utterance
            with self._make_stream():
                _capture()
        if not self._frames:
            log.warning("no audio frames captured")
            return None
        return np.concatenate(self._frames)

    @staticmethod
    def duration(audio: np.ndarray | None, sample_rate: int) -> float:
        return 0.0 if audio is None else len(audio) / sample_rate
