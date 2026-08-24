"""faster-whisper wrapper: model loaded once, stays resident in RAM."""

import logging
import time

import numpy as np

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, model_name: str, device: str = "cpu",
                 compute_type: str = "int8"):
        from faster_whisper import WhisperModel   # heavy import, defer it
        t0 = time.time()
        log.info("loading %s (%s/%s)...", model_name, device, compute_type)
        self._model = WhisperModel(model_name, device=device,
                                   compute_type=compute_type)
        log.info("model ready in %.1fs", time.time() - t0)

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   language: str | None = "en") -> str:
        """audio: float32 mono [n, 1]. Returns stripped text ('' if silence)."""
        if audio is None or len(audio) == 0:
            return ""
        t0 = time.time()
        segments, info = self._model.transcribe(
            audio.flatten(),
            language=language,
            vad_filter=True,             # skip silence, fewer hallucinations
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info("'%s' (%.1fs for %.1fs audio)",
                 text, time.time() - t0, info.duration)
        return text
