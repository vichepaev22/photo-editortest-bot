import json
import os
import time
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "data" / "runtime"


class AlreadyRunning(ValueError):
    def __init__(self):
        super().__init__("bot_already_running")


class _Lock:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.handle = (root / "bot.lock").open("a+b")

    def acquire(self):
        try:
            if os.name == "nt":
                import msvcrt

                if self.handle.seek(0, 2) == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise AlreadyRunning() from None

    def close(self):
        self.handle.close()  # OS releases the exclusive lock, including on process death.


def status(root=RUNTIME_DIR):
    lock = _Lock(root)
    try:
        lock.acquire()
    except AlreadyRunning:
        running = True
    else:
        running = False
        lock.close()
    try:
        saved = json.loads((root / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {k: saved.get(k) for k in ("pid", "mode", "state", "bot_username", "updated_at")} | {
        "running": running,
    }


class BotRuntime:
    def __init__(self, root, mode):
        self.root, self.mode = Path(root), mode
        self.username = None

    def _write(self, state):
        payload = {
            "pid": os.getpid(),
            "mode": self.mode,
            "state": state,
            "bot_username": self.username,
            "updated_at": time.time(),
        }
        temporary = self.root / f"status.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(self.root / "status.json")

    def __enter__(self):
        self.lock = _Lock(self.root)
        self.lock.acquire()
        try:
            (self.root / "stop.request").unlink(missing_ok=True)
            self._write("starting")
        except BaseException:
            self.lock.close()
            raise
        return self

    def ready(self, username):
        self.username = username
        self._write("ready")

    def request_stop(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "stop.request").touch()

    def stop_requested(self):
        return (self.root / "stop.request").exists()

    def __exit__(self, kind, value, traceback):
        try:
            self._write("failed" if kind else "stopped")
        finally:
            self.lock.close()
