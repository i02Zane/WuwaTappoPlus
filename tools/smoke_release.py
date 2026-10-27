"""Exercise a built EXE without touching policies or the real desktop pet."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wuwa_tappo_plus import __version__, win
from wuwa_tappo_plus.config import DEFAULT_TOML


def main():
    executable = ROOT / "dist/WuwaTappoPlus.exe"
    base = ROOT / ".smoke-data"
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-", dir=base) as folder, \
            socket.socket() as unused_port:
        data = Path(folder)
        unused_port.bind(("127.0.0.1", 0))  # Bound, not listening: no real CDP connection.
        port = unused_port.getsockname()[1]
        config = DEFAULT_TOML.replace("port = 9222", "port = %d" % port)
        config = config.replace("max_runtime = 0", "max_runtime = 2").replace("watchdog_min = 1", "watchdog_min = 0")
        (data / "config.toml").write_text(config, encoding="utf-8")
        env = {**os.environ, "WUWA_TAPPO_PLUS_DATA_DIR": str(data)}
        # A renamed EXE copied alone into a separate directory must still work.
        copied = data / "Wuwa Tappo Plus.exe"
        shutil.copy2(executable, copied)
        executable = copied

        def run(*args, timeout=15):
            return subprocess.run([str(executable), *map(str, args)], cwd=data, env=env,
                                  check=True, timeout=timeout)

        run("--output", data / "probe.json", "--probe")
        probe = json.loads((data / "probe.json").read_text(encoding="utf-8"))
        assert probe["frozen"] and probe["version"] == __version__
        assert Path(probe["data_dir"]) == data
        assert set(probe["scripts"]) == {"pet", "settings", "rooms", "toast"}
        assert all(version == __version__ for version in probe["scripts"].values())
        assert len(probe["licenses"]) == 6
        run("--output", data / "help.txt", "help")
        assert "v" + __version__ in (data / "help.txt").read_text(encoding="utf-8")
        run("--console-smoke", data / "console.json")
        console = json.loads((data / "console.json").read_text(encoding="utf-8"))
        assert console["console"] and console["stdin_tty"] and console["stdout_tty"]
        print("EXE entry, resources and terminal menu passed.", flush=True)

        # Exercise the exact command the watchdog uses, against isolated state.
        pid = None
        try:
            run("ensure")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                state_file = data / ".state.json"
                if state_file.exists():
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    pid = state.get("pid")
                    assert state["plus_version"] == __version__ and state["port"] == port
                    resources = Path(state["resource_dir"])
                    assert all((resources / name).is_file()
                               for name in ("inject.js", "settings.js", "rooms.js", "toast.js"))
                    break
                time.sleep(0.1)
            assert pid, "watchdog did not start the frozen daemon"
            deadline = time.monotonic() + 12
            while win.pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not win.pid_alive(pid), "isolated daemon did not honor max_runtime"
            print("Windowless watchdog -> frozen daemon -> timed exit passed.", flush=True)
        finally:
            if pid and win.pid_alive(pid):
                (data / ".stop").write_text("smoke-test cleanup", encoding="utf-8")
                deadline = time.monotonic() + 5
                while win.pid_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
        # The Python daemon exits before the onefile bootloader finishes its
        # extraction cleanup. Wait for that parent to release the copied EXE.
        deadline = time.monotonic() + 8
        while copied.exists():
            try:
                copied.unlink()
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

    released = ROOT / "dist/WuwaTappoPlus.exe"
    expected = "%s  %s\n" % (hashlib.sha256(released.read_bytes()).hexdigest(), released.name)
    assert (ROOT / "dist/SHA256SUMS.txt").read_text(encoding="utf-8") == expected
    print("Release checksum matches the EXE; no separate source archive is required.")


if __name__ == "__main__":
    main()
