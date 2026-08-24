"""Microphone capture via sounddevice/PortAudio.

Capture reliability comes from two buffers:

  pre-roll ring buffer - while the stream is open we ALWAYS keep the last
      preroll_ms of audio in a deque. When the hotkey fires, those frames
      are prepended to the recording, so speech that started before you
      pressed the key survives ("s is..." <- "This is..." bug).

  tail padding - after the stop signal we keep buffering tail_padding_ms,
      so pressing the hotkey mid-word doesn't chop trailing consonants
      ("testin" <- "testing" bug).

Two capture modes:
  keep_mic_open=True: one persistent InputStream; zero delay on hotkey.
      Side effect: GNOME shows its mic-in-use dot permanently.
  keep_mic_open=False: stream opens per utterance (privacy-friendly).
"""

import logging
import time
from collections import deque
from collections.abc import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, sample_rate: int = 16000, device: int | None = None,
                 keep_mic_open: bool = True, preroll_ms: int = 600):
        self.sample_rate = sample_rate
        self.device = device
        self.keep_mic_open = keep_mic_open
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_max_samples = max(1, int(sample_rate * preroll_ms / 1000))
        self._preroll_samples = 0
        self._frames: list[np.ndarray] = []
        self._capturing = False
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            log.warning("audio overflow: %s", status)
        self._preroll.append(indata.copy())     # always, hotkey or not
        self._preroll_samples += len(indata)
        # trim oldest whole blocks until we're back inside the time budget
        while (self._preroll_samples > self._preroll_max_samples
               and len(self._preroll) > 1):
            self._preroll_samples -= len(self._preroll.popleft())
        if self._capturing:
            self._frames.append(indata.copy())

    def _make_stream(self) -> sd.InputStream:
        return sd.InputStream(samplerate=self.sample_rate, channels=1,
                              dtype="float32", device=self.device,
                              callback=self._callback,
                              blocksize=512)    # small blocks = tight timing

    # ── persistent mode ──────────────────────────────────────────────────────
    def open(self) -> None:
        """Open the stream now so recording later starts with zero delay."""
        if self._stream is not None:
            return
        self._stream = self._make_stream()
        self._stream.start()
        log.info("mic stream open (%d Hz, always-on, %.0fms preroll)",
                 self.sample_rate,
                 1000 * self._preroll_max_samples / self.sample_rate)

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
        """
        # seed with pre-hotkey audio so early speech isn't lost
        self._frames = list(self._preroll)

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
            # lazy mode: no stream yet -> preroll empty, open per utterance
            with self._make_stream():
                _capture()
        if not self._frames:
            log.warning("no audio frames captured")
            return None
        return np.concatenate(self._frames)

    @staticmethod
    def duration(audio: np.ndarray | None, sample_rate: int) -> float:
        return 0.0 if audio is None else len(audio) / sample_rate
