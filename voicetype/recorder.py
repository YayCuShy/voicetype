"""Microphone capture via sounddevice/PortAudio.

The PortAudio callback runs on its own thread ~30x/s; the main thread only
flips `capturing` and blocks in `wait_stop` until told to stop.
"""

import logging
from collections.abc import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, sample_rate: int = 16000,
                 device: int | None = None):
        self.sample_rate = sample_rate
        self.device = device
        self._frames: list[np.ndarray] = []
        self._capturing = False

    def _callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            log.warning("audio overflow: %s", status)
        if self._capturing:
            self._frames.append(indata.copy())

    def record_until(self, wait_stop: Callable[[], None]) -> np.ndarray | None:
        """Capture mic audio until wait_stop() returns. Returns [n,1] float32.

        wait_stop is any blocking call that returns when recording should
        stop - e.g. threading.Event.wait or threading.Semaphore.acquire.
        """
        self._frames = []
        self._capturing = True
        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1,
                                dtype="float32", device=self.device,
                                callback=self._callback,
                                blocksize=0):          # let PortAudio choose
                wait_stop()
        finally:
            self._capturing = False
        if not self._frames:
            log.warning("no audio frames captured")
            return None
        return np.concatenate(self._frames)

    @staticmethod
    def duration(audio: np.ndarray | None, sample_rate: int) -> float:
        return 0.0 if audio is None else len(audio) / sample_rate
