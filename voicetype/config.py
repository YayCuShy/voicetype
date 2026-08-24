"""Configuration: defaults < ~/.config/voicetype/config.toml < CLI flags."""

import logging
import sys
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "voicetype" / "config.toml"


@dataclass
class Config:
    model: str = "small.en"
    language: str = "en"
    device: str = "cpu"                # 'cpu' or 'cuda'
    compute_type: str = "int8"         # int8 fast on CPU, float16 on GPU
    sample_rate: int = 16000           # whisper-native rate
    min_seconds: float = 0.4           # ignore accidental taps
    mic_device: int | None = None      # None = system default input
    ydotool_delay: int = 12            # ms between keystrokes (fallback path)
    paste_binding: str = "ctrl+v"      # combo used to paste; terminals want ctrl+shift+v
    quiet: bool = False                # suppress desktop notifications
    tray: bool = False                 # tray icon needs AppIndicator ext
    keep_mic_open: bool = True         # pre-opened stream: no first-word clipping


def load_config(cli_overrides: dict | None = None) -> Config:
    values: dict = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                values.update(tomllib.load(f))
        except tomllib.TOMLDecodeError as e:
            log.warning("bad config %s (%s), using defaults", CONFIG_PATH, e)
    if cli_overrides:
        values.update({k: v for k, v in cli_overrides.items() if v is not None})

    known = {f.name for f in fields(Config)}
    unknown = set(values) - known
    if unknown:
        log.warning("ignoring unknown config keys: %s", sorted(unknown))

    cfg = Config(**{k: v for k, v in values.items() if k in known})

    # basic validation with actionable errors
    if cfg.sample_rate not in (16000,) and cfg.device == "cpu":
        log.warning("sample_rate %d will be resampled by whisper; prefer 16000", cfg.sample_rate)
    if cfg.min_seconds < 0:
        sys.exit("config error: min_seconds must be >= 0")
    return cfg
