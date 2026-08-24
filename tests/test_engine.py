"""Engine tests against the real (cached) base.en model - kept small & slow.

Run with: pytest tests/ -v          (all)
Skip via: pytest tests/ -m "not slow"
"""

import numpy as np
import pytest

from voicetype.engine import Engine
from voicetype.recorder import Recorder

pytestmark = pytest.mark.slow
MODEL = "base.en"      # smallest cached model; keeps tests fast


@pytest.fixture(scope="module")
def engine():
    return Engine(MODEL, device="cpu", compute_type="int8")


def test_silence_transcribes_to_empty(engine):
    one_second = np.zeros((16_000, 1), dtype="float32")
    assert engine.transcribe(one_second, 16000) == ""


def test_empty_audio_returns_empty(engine):
    assert engine.transcribe(np.empty((0, 1), dtype="float32"), 16000) == ""


def test_tone_does_not_crash(engine):
    t = np.arange(8_000, dtype="float32") / 16_000
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    text = engine.transcribe(tone.reshape(-1, 1), 16000)
    assert isinstance(text, str)


def test_recorder_duration_helper():
    assert Recorder.duration(None, 16000) == 0.0
    assert Recorder.duration(np.zeros((32000, 1), "float32"), 16000) == 2.0
