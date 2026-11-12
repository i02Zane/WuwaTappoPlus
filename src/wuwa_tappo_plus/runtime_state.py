"""Daemon instance lock and atomic, read-only-to-consumers status publication."""

import json
import os
import time
from datetime import datetime

from . import __version__
from .config import INJECT_PATH, LOCK_PATH, STATE_PATH


def remaining(snapshot, name, now):
    """A hidden native countdown is unknown, not an epoch-sized overdue value."""
    timestamp = snapshot.get(name)
    return round((timestamp - now) / 1000.0, 1) if timestamp else None


class InstanceLock:
    def __init__(self, path=LOCK_PATH):
        self.path = path
        self._lock_handle = None

    def acquire(self):
        # On Windows an already-locked file fails at open() time with a sharing
        # violation, so the open itself has to be inside the guard.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+")
        except OSError:
            return False
        try:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self._lock_handle = handle
        return True

    def release(self):
        if self._lock_handle is None:
            return
        try:
            import msvcrt

            self._lock_handle.seek(0)
            msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            self._lock_handle.close()
        except OSError:
            pass
        self._lock_handle = None


class StatePublisher:
    def __init__(self, path=STATE_PATH):
        self.path = path

    def _write(self, payload):
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def publish(self, cfg, session, observations, snapshot=None, extra=None):
        payload = {
            "plus_version": __version__,
            "resource_dir": str(INJECT_PATH.parent),
            "pid": os.getpid(),
            "started": observations.started.isoformat(timespec="seconds"),
            "updated": datetime.now().isoformat(timespec="seconds"),
            "port": cfg.port,
            "target": (session.target or {}).get("url"),
            "clicks_logged": observations.click_count,
            "alert": observations.alert_level or 0,
            "auto_open": cfg.auto_open,
        }
        if snapshot:
            now = snapshot.get("now") or int(time.time() * 1000)
            payload.update({
                "now": now,
                "left": snapshot.get("left"),
                "right": snapshot.get("right"),
                "remain_left": remaining(snapshot, "left", now),
                "remain_right": remaining(snapshot, "right", now),
                "balance": snapshot.get("bal"),
                "inject": snapshot.get("inject", 0),
            })
        if extra:
            payload.update(extra)
        self._write(payload)

    def stopped(self):
        # Do not recreate status after an uninstall has already removed it.
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data["pid"] = None
            data["stopped"] = datetime.now().isoformat(timespec="seconds")
        except (OSError, ValueError, TypeError):
            return
        self._write(data)
