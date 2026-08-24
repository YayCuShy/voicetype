"""Desktop notifications (best-effort; never fatal)."""

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


def notify(title: str, body: str = "", quiet: bool = False) -> None:
    if quiet or shutil.which("notify-send") is None:
        return
    try:
        subprocess.run(["notify-send", title, body], timeout=3, check=False)
    except subprocess.TimeoutExpired:
        log.debug("notify-send timed out")
