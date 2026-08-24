"""Config precedence: defaults < toml file < CLI overrides."""

from voicetype.config import Config, load_config


def test_defaults():
    cfg = load_config()
    assert cfg.model == "small.en"
    assert cfg.language == "en"
    assert cfg.device == "cpu"
    assert cfg.mic_device is None


def test_cli_overrides_beat_defaults(monkeypatch):
    monkeypatch.setattr("voicetype.config.CONFIG_PATH",
                        __import__("pathlib").Path("/nonexistent.toml"))
    cfg = load_config({"model": "tiny.en", "language": None})
    assert cfg.model == "tiny.en"       # set override applied
    assert cfg.language == "en"         # None override ignored


def test_unknown_keys_are_ignored_not_fatal(tmp_path, monkeypatch):
    bad = tmp_path / "config.toml"
    bad.write_text('model = "base.en"\nfuture_option = true\n')
    monkeypatch.setattr("voicetype.config.CONFIG_PATH", bad)
    cfg = load_config()
    assert cfg.model == "base.en"


def test_broken_toml_falls_back_to_defaults(tmp_path, monkeypatch):
    bad = tmp_path / "config.toml"
    bad.write_text("model = [unclosed")
    monkeypatch.setattr("voicetype.config.CONFIG_PATH", bad)
    assert load_config().model == Config().model
