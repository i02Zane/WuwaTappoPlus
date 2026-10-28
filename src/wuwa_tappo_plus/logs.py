"""Rolling file logger. Kept tiny on purpose: the daemon has no console."""

import os
import time

from .config import LOG_DIR

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
MAX_ENTRY_BYTES = 16 * 1024


class Log:
    def __init__(self, max_bytes=1048576, keep=3, path=None):
        self.path = path or (LOG_DIR / "auto.log")
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.keep = max(1, int(keep))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate(self, incoming=0):
        try:
            if self.path.stat().st_size + incoming <= self.max_bytes:
                return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

        oldest = self.path.with_name(self.path.name + ".%d" % self.keep)
        if oldest.exists():
            try:
                oldest.unlink()
            except OSError:
                pass

        for i in range(self.keep - 1, 0, -1):
            src = self.path.with_name(self.path.name + ".%d" % i)
            dst = self.path.with_name(self.path.name + ".%d" % (i + 1))
            if src.exists():
                try:
                    os.replace(src, dst)
                except OSError:
                    pass

        try:
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            return True
        except OSError:
            return False

    def write(self, message, level="INFO", echo=False):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = "%s %-5s %s" % (stamp, level, message)
        encoded = line.encode("utf-8")
        if len(encoded) + 1 > MAX_ENTRY_BYTES:
            suffix = b"... [truncated]"
            line = encoded[:MAX_ENTRY_BYTES - 1 - len(suffix)].decode("utf-8", errors="ignore") + suffix.decode()
        encoded = (line + "\n").encode("utf-8")
        if self._rotate(len(encoded)):
            try:
                with open(self.path, "ab") as f:
                    f.write(encoded)
            except OSError:
                pass
        if echo:
            try:
                print(line, flush=True)
            except Exception:
                pass

    def info(self, message, echo=False):
        self.write(message, "INFO", echo)

    def warn(self, message, echo=False):
        self.write(message, "WARN", echo)

    def error(self, message, echo=False):
        self.write(message, "ERROR", echo)

    def tail(self, lines=40):
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                rows = f.read().splitlines()
        except OSError:
            return []
        return rows[-lines:]
