"""PID file management with stale-process detection.

A PID file is only trustworthy if the process it names is still alive and is
actually a voicetype daemon (we tag /proc/<pid>/cmdline to be sure we never
signal an unrelated process that recycled the PID).
"""

import logging
import os
import pathlib

log = logging.getLogger(__name__)

TAG = "voicetype-daemon"


def pid_file() -> pathlib.Path:
    run_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return pathlib.Path(run_dir) / "voicetype.pid"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)          # signal 0 = existence check only
        return True
    except ProcessLookupError:
        return False
    except PermissionError:      # exists but owned by someone else
        return True


def _is_voicetype(pid: int) -> bool:
    """Defend against PID reuse by an unrelated process.

    Accepts every legitimate launch mode. Note the kernel rewrites argv[0]
    to the interpreter for shebang scripts, so a console script's cmdline
    looks like '<venv>/python3 <venv>/bin/voicetype daemon' - hence we
    must scan all argv slots, not just argv[0].
    """
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    if b"-m voicetype" in raw:
        return True
    return any(n in (b"voicetype", b"__main__.py")
               or n.startswith(b"voicetype.")
               for n in (os.path.basename(a) for a in raw.split(b"\0") if a))


def read_pid(path: pathlib.Path | None = None) -> int | None:
    """Return a live voicetype PID, or None if file missing/stale/garbage."""
    path = path or pid_file()
    try:
        raw = path.read_text().strip()
        pid = int(raw.split()[0])     # payload is "<pid> <tag>"
    except FileNotFoundError:
        return None
    except (ValueError, IndexError):
        log.warning("corrupt pid file %s (%r) - removing", path, raw[:20])
        path.unlink(missing_ok=True)
        return None
    if not _alive(pid):
        log.info("stale pid file (pid %d gone) - removing", pid)
        path.unlink(missing_ok=True)
        return None
    if not _is_voicetype(pid):
        log.warning("pid %d alive but not voicetype - removing file", pid)
        path.unlink(missing_ok=True)
        return None
    return pid


def claim_pid_file(setproctitle_cmdline: str) -> pathlib.Path | None:
    """Atomically create the PID file. Returns None if another daemon won."""
    path = pid_file()
    if read_pid(path) is not None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{os.getpid()} {setproctitle_cmdline}\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return None              # raced another starter; they won
    with os.fdopen(fd, "w") as f:
        f.write(payload)
    return path


def release_pid_file(path: pathlib.Path | None = None) -> None:
    path = path or pid_file()
    try:
        content = path.read_text()
        mine = content.split()[0] == str(os.getpid())
    except (FileNotFoundError, IndexError):
        return
    if mine:
        path.unlink(missing_ok=True)
