# voicetype

Minimal, fully offline voice dictation for Linux (Wayland & X11). Press a global hotkey, speak, press again — your words are transcribed locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and typed into whatever window has focus: IDE, terminal, browser, chat, anything.

No cloud. No API keys. No subscription. Audio never leaves your machine.

Built as a learning project after studying [talkietypie](https://github.com/pietervwyk/talkietypie) — same core architecture (~1500 lines), distilled to ~600 readable lines with a test suite.

## Features

- **100% local** — faster-whisper (`small.en`, int8 quantized) runs on CPU in real time (~2–4 s per utterance)
- **Global hotkey** — works from any app via GNOME custom shortcuts (or any launcher that can run a command)
- **Tray indicator** — colored dot in your top bar: grey = ready, red = recording (mic live), orange = transcribing
- **Daemon architecture** — the model loads once and stays resident in RAM (~500 MB); toggling is instant
- **Robust text injection** — fallback chain `ydotool → wtype → clipboard paste`, with clipboard save/restore so dictation never destroys what you had copied
- **Safe lifecycle** — atomic PID-file claims, stale-process detection, and identity defense against PID recycling (won't signal an unrelated process that happens to reuse the daemon's PID)
- **Configurable** — TOML config file plus per-invocation CLI overrides
- **Tested** — 22 pytest tests covering config precedence, injection orchestration, PID logic, and the transcription engine

## Requirements

| Requirement | Notes |
|---|---|
| Linux | Developed on Ubuntu 25.10 / GNOME / Wayland |
| Python | ≥ 3.11 |
| Microphone | Any input device visible to PipeWire/PulseAudio |
| PortAudio dev headers | For `sounddevice` |
| `ydotool` | Text injection via `/dev/uinput` (works on all compositors) |
| `wtype`, `wl-clipboard` | Optional injection fallbacks |

CPU-only is fine. An NVIDIA GPU (CUDA) speeds things up if you set `device = "cuda"`, `compute_type = "float16"`.

## Installation

### 1. System packages (Ubuntu/Debian)

```bash
sudo apt install portaudio19-dev ffmpeg ydotool wtype wl-clipboard libnotify-bin
```

<details>
<summary>Fedora</summary>

```bash
sudo dnf install portaudio-devel ffmpeg ydotool wtype wl-clipboard libnotify
```
</details>

### 2. Make `/dev/uinput` writable by your user

`ydotool` synthesizes keystrokes through the kernel's uinput device, which is root-only by default:

```bash
echo 'KERNEL=="uinput", MODE="0666", GROUP="input"' | sudo tee /etc/udev/rules.d/60-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

> Single-user desktops only. On multi-user machines prefer a `ydotoold` daemon setup instead of world-writable uinput.

### 3. Install voicetype

```bash
git clone https://github.com/YayCuShy/voicetype.git
cd voicetype
python3 -m venv .venv
.venv/bin/pip install -e .
```

The Whisper model downloads automatically on first run (~460 MB for `small.en`, cached in `~/.cache/huggingface/`).

### 4. Bind a global hotkey

The client command `voicetype toggle` auto-starts the daemon if it isn't running, then starts/stops recording. Bind it once:

**GUI:** Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts → add:

```
Name:    voicetype
Command: /path/to/voicetype/.venv/bin/voicetype toggle
Binding: Ctrl+Alt+V        (anything free works)
```

**CLI (GNOME):**

```bash
KB=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom8/
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings | sed "s|]|, '$KB']|")"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB name 'voicetype'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB binding '<Primary><Alt>v'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB command "$PWD/.venv/bin/voicetype toggle"
```

## Usage

```bash
voicetype daemon     # start the daemon in the foreground
voicetype toggle     # start/stop recording (hotkey target; auto-starts daemon)
voicetype status     # is the daemon alive?
voicetype stop       # terminate it
voicetype list-mics  # show input device indices (for mic_device config)
```

Typical flow: focus any text field → **Ctrl+Alt+V** → speak → **Ctrl+Alt+V** → text appears at your cursor.

### Run as a systemd user service (recommended)

Keeps the daemon warm across reboots and restarts it on crashes:

```ini
# ~/.config/systemd/user/voicetype.service
[Unit]
Description=voicetype - minimal offline dictation daemon
After=graphical-session.target pipewire.service

[Service]
Type=simple
ExecStart=%h/path/to/voicetype/.venv/bin/voicetype daemon --quiet
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now voicetype.service
journalctl --user -u voicetype.service -f   # follow logs
```

## Configuration

Create `~/.config/voicetype/config.toml`. All keys optional:

| Key | Default | Description |
|---|---|---|
| `model` | `"small.en"` | Any faster-whisper model: `tiny.en`, `base.en`, `small.en`, `medium.en`, `large-v3-turbo`, … Drop the `.en` suffix (e.g. `"small"`) for multilingual support |
| `language` | `"en"` | ISO code, or `"auto"` to detect |
| `device` | `"cpu"` | `"cuda"` for NVIDIA GPU |
| `compute_type` | `"int8"` | `int8` best for CPU, `float16` for GPU |
| `sample_rate` | `16000` | Leave at whisper-native 16 kHz |
| `min_seconds` | `0.4` | Utterances shorter than this are discarded |
| `tail_padding_ms` | `350` | Keep recording this long after the stop hotkey so trailing consonants aren't chopped |
| `preroll_ms` | `600` | Mic audio buffered *before* the hotkey press and prepended to the recording — speech that starts early survives. Only active with `keep_mic_open = true` |
| `save_audio` | `true` | Archive each utterance (WAV + transcript) to `~/.local/share/voicetype/sessions/` for debugging |
| `mic_device` | `null` | Input index from `voicetype list-mics`, or null for system default |
| `ydotool_delay` | `12` | Milliseconds between injected keystrokes (fallback path only) |
| `paste_binding` | `"ctrl+v"` | Combo used to paste from clipboard — the primary injection method, safe for any Unicode/layout. Use `"ctrl+shift+v"` if you mainly dictate into terminals |
| `quiet` | `false` | Suppress desktop notifications |
| `tray` | `false` | System tray icon (requires the Ubuntu AppIndicators extension) |
| `keep_mic_open` | `true` | Keep the mic stream open between utterances — recording starts instantly, no first-word clipping. Side effect: GNOME's mic-in-use dot stays on. Set `false` for privacy-friendly per-utterance capture |

Example — multilingual, GPU-accelerated:

```toml
model = "large-v3-turbo"
language = "auto"
device = "cuda"
compute_type = "float16"
```

Every key can be overridden per invocation:

```bash
voicetype daemon --model tiny.en --language es --min-seconds 0.6
```

Precedence: defaults < `config.toml` < CLI flags.

## How it works

```
Ctrl+Alt+V ──> `voicetype toggle` ──> SIGUSR1 ──> daemon
                                                   │
                    ┌──────────────────────────────┘
                    ▼
      IDLE ──toggle──> RECORDING ──toggle──> TRANSCRIBING ──> inject text ──> IDLE
        ▲              PortAudio             faster-whisper        │
        └────────────────── semaphore queues every toggle ────────┘
```

1. **Hotkey layer** — GNOME runs `voicetype toggle`; the OS does the hard part of global capture.
2. **Client** — validates the daemon's PID file (alive? actually ours?), spawns the daemon detached if needed, delivers `SIGUSR1`.
3. **Daemon** — signal handlers only release a counting *semaphore*; the main loop owns all state transitions, so toggles arriving mid-transcription queue up instead of getting lost.
4. **Recorder** — a PortAudio callback thread appends 16 kHz float32 chunks while the main thread blocks on the semaphore.
5. **Engine** — one faster-whisper instance, loaded once, reused forever. VAD filtering skips silence and reduces hallucinations.
6. **Injector** — tries direct keystroke synthesis first (`ydotool`), then Wayland's virtual-keyboard protocol (`wtype`), finally clipboard-paste with save/restore.

Why not just `wtype`? GNOME's Mutter doesn't implement the virtual-keyboard protocol, hence the chain.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Text never appears | `tail /tmp/voicetype.log` — look for injection errors; verify `ls -l /dev/uinput` shows group/other write access |
| `wtype failed: Compositor does not support virtual keyboard protocol` | Expected on GNOME — the chain falls through to ydotool automatically |
| Daemon won't start | Another instance may hold the PID file: `voicetype status`, then `kill $(cut -d' ' -f1 $XDG_RUNTIME_DIR/voicetype.pid)` |
| Terminal gets nothing | Clipboard-fallback pastes use plain Ctrl+V; some terminals want Ctrl+Shift+V |
| Model download fails | Check connectivity to huggingface.co; delete `~/.cache/huggingface/hub/models--Systran--*` to retry cleanly |
| Missing first words | Keep `keep_mic_open = true` (default) so capture starts the instant you press the hotkey |
| Accented garbage instead of apostrophes (`youŕe`) | Keystroke fallback reinterprets keycodes through your keyboard layout — paste mode should have handled it; check `paste_binding` matches your target app |
| Nothing pastes in terminal | Set `paste_binding = "ctrl+shift+v"`; plain Ctrl+V is literal-insert in most terminals |
| Wrong microphone | Run `voicetype list-mics`, put the index in `mic_device` |
| No tray icon | Ubuntu: `sudo apt install gir1.2-ayatanaappindicator3-0.1` and make sure the *Ubuntu AppIndicators* extension is enabled; also check the daemon wasn't started with `--no-tray` |
| `Gtk-CRITICAL ... gtk_widget_get_scale_factor` spam in logs | Harmless GNOME Shell/AppIndicator polling noise — safe to ignore |
| Everything broken | `voicetype stop && systemctl --user restart voicetype.service && journalctl --user -u voicetype -f` |

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v                 # full suite
pytest tests/ -m "not slow"      # skip real-model engine tests
```

```
voicetype/
├── __main__.py    # CLI parsing, logging setup
├── config.py      # dataclass config, TOML + CLI merge
├── pidfile.py     # atomic claims, stale detection, identity defense
├── daemon.py      # semaphore state machine, client ops
├── recorder.py    # PortAudio capture
├── engine.py      # faster-whisper wrapper
├── inject.py      # injection fallback chain
└── notify.py      # desktop notifications
tests/
├── test_pidfile.py
├── test_inject.py
├── test_config.py
└── test_engine.py # loads real base.en model (marked slow)
```

## License

[MIT](LICENSE) © 2026 Javier Quejigo Calatayud
