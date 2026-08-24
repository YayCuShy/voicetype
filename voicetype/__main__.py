"""CLI entrypoint.

    python -m voicetype                 start daemon (foreground)
    python -m voicetype toggle          toggle recording (auto-starts daemon)
    python -m voicetype stop | status   lifecycle helpers
"""

import argparse
import logging
import sys

from . import __version__
from .config import Config, load_config

LOG_FILE = "/tmp/voicetype.log"


def setup_logging(verbose: bool) -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [logging.FileHandler(LOG_FILE, "a")]
    if sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="voicetype",
                                description="offline voice dictation")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    def common(sp):
        sp.add_argument("--model", help="whisper model (default: small.en)")
        sp.add_argument("--language", help="'en', 'es', or 'auto'")
        sp.add_argument("--device", choices=["cpu", "cuda"])
        sp.add_argument("--compute-type",
                        choices=["int8", "float16", "float32"])
        sp.add_argument("--min-seconds", type=float)
        sp.add_argument("--mic", type=int, metavar="IDX", dest="mic_device",
                        help="input device index (see --list-mics)")
        sp.add_argument("--quiet", action="store_true",
                        help="no desktop notifications")
        sp.add_argument("--no-tray", action="store_true",
                        help="disable the system tray icon")

    common(sub.add_parser("daemon", help="run daemon in foreground"))
    t = sub.add_parser("toggle", help="start/stop recording")
    common(t)
    sub.add_parser("stop", help="terminate daemon")
    sub.add_parser("status", help="show daemon state")

    mics = sub.add_parser("list-mics", help="show input devices")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "list-mics":
        import sounddevice as sd
        print(sd.query_devices())
        return 0

    if args.command in ("stop", "status"):
        from .daemon import client
        return client(args.command)

    overrides = {
        "model": getattr(args, "model", None),
        "language": getattr(args, "language", None),
        "device": getattr(args, "device", None),
        "compute_type": getattr(args, "compute_type", None),
        "min_seconds": getattr(args, "min_seconds", None),
        "mic_device": getattr(args, "mic_device", None),
        "quiet": True if getattr(args, "quiet", False) else None,
        "tray": False if getattr(args, "no_tray", False) else None,
    }
    cfg = load_config(overrides)

    from .daemon import Daemon, client
    if args.command == "toggle":
        return client("toggle")
    Daemon(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
