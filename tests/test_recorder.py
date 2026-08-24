"""Recorder pre-roll ring buffer: early speech must survive the hotkey."""

import threading

import numpy as np

from voicetype.recorder import Recorder


class FakeStop:
    """Callable that returns immediately - stands in for Semaphore.acquire."""
    def __call__(self):
        pass


def _mk_recorder(preroll_ms=600, sr=16000):
    return Recorder(sample_rate=sr, keep_mic_open=True, preroll_ms=preroll_ms)


def test_preroll_seeds_recording_with_pre_hotkey_audio():
    r = _mk_recorder()
    early = np.full((1600, 1), 0.5, dtype="float32")   # 100ms of "speech"
    r._preroll.append(early)                            # arrived before hotkey
    # no live stream -> record_until uses lazy path; simulate capture window
    r._stream = object()                                # force persistent path
    frames = r.record_until(FakeStop())
    assert frames is not None
    assert len(frames) == 1600                          # the pre-hotkey frame
    assert (frames == 0.5).all()


def test_preroll_ring_never_grows_unbounded():
    sr = 16000
    r = _mk_recorder(preroll_ms=100, sr=sr)
    block = np.zeros((1600, 1), dtype="float32")        # 100ms blocks
    for _ in range(50):                                 # 5s of audio
        r._callback(block, None, None, None)
    total_samples = sum(len(b) for b in r._preroll)
    assert total_samples <= int(sr * 0.1) + 1600        # budget + one block


def test_tail_padding_extends_capture(monkeypatch):
    r = _mk_recorder()
    r._stream = object()
    slept = []
    monkeypatch.setattr("voicetype.recorder.time.sleep",
                        lambda s: slept.append(s))
    r.record_until(FakeStop(), tail_padding_ms=350)
    assert 0.35 in slept
