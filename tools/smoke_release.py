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
from wuwa_tappo_plus.script_source import script_files


def main():
    installer = ROOT / "dist/WuwaTappoPlus-Setup.exe"
    payload = ROOT / "build/payload/WuwaTappoPlus"
    assert not any(path.name.lower() == "ucrtbase.dll" for path in payload.rglob("*")), \
        "The payload must use the Windows UCRT instead of bundling ucrtbase.dll"
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
        # Copy the entire directory payload; daily startup never self-extracts.
        application = data / "WuwaTappoPlus"
        shutil.copytree(payload, application)
        copied = application / "Wuwa Tappo Plus.exe"
        installed_exe = application / "WuwaTappoPlus.exe"
        shutil.copy2(installed_exe, copied)
        for path in (copied, installed_exe):
            subprocess.run(["icacls", str(path), "/setintegritylevel", "M"],
                           check=True, capture_output=True)
        executable = copied
        setup_report = data / "installer-probe.json"
        subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                        "/VALIDATEONLY=" + str(setup_report)], cwd=data, env=env, timeout=45)
        extracted = json.loads(setup_report.read_text(encoding="utf-8"))
        assert extracted["frozen"] and extracted["version"] == __version__
        assert extracted["scripts"] == dict.fromkeys(
            ("pet", "settings", "rooms", "toast", "game_mode", "plaza"), __version__)
        print("Inno installer extraction and payload probe passed without installation.", flush=True)

        def run(*args, timeout=15):
            return subprocess.run([str(executable), *map(str, args)], cwd=data, env=env,
                                  check=True, timeout=timeout)

        run("--output", data / "probe.json", "--probe")
        probe = json.loads((data / "probe.json").read_text(encoding="utf-8"))
        assert probe["frozen"] and probe["version"] == __version__
        assert Path(probe["data_dir"]) == data
        assert set(probe["scripts"]) == {"pet", "settings", "rooms", "toast", "game_mode", "plaza"}
        assert all(version == __version__ for version in probe["scripts"].values())
        assert len(probe["licenses"]) == 7
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
                    packaged_scripts = {path.name for path in resources.iterdir()
                                        if path.suffix in (".js", ".json")}
                    assert packaged_scripts == set(script_files()), "script resources differ from manifest"
                    break
                time.sleep(0.1)
            assert pid, "watchdog did not start the frozen daemon"
            deadline = time.monotonic() + 12
            while win.pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not win.pid_alive(pid), "isolated daemon did not honor max_runtime"
            print("Windowless watchdog -> frozen daemon -> timed exit passed.", flush=True)

            # No --restart-app: setup must complete without opening the real pet
            # or waiting for the deliberately unavailable debugging port.
            run("--program-dir", application, "--output", data / "setup-start.txt",
                "--installer", "start", "--setup-source", installer)
            receipt = json.loads((application / "install.json").read_text(encoding="utf-8"))
            assert receipt["manager"] == "inno-setup" and receipt["pending"] is False
            assert "桌宠原本未运行" in (data / "setup-start.txt").read_text(encoding="utf-8")
            pid = json.loads(state_file.read_text(encoding="utf-8")).get("pid")
            assert pid, "setup did not confirm the background daemon"
            deadline = time.monotonic() + 12
            while win.pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not win.pid_alive(pid), "setup daemon did not honor max_runtime"
            print("Setup completes with pet closed; isolated background startup passed.", flush=True)
        finally:
            if pid and win.pid_alive(pid):
                (data / ".stop").write_text("smoke-test cleanup", encoding="utf-8")
                deadline = time.monotonic() + 5
                while win.pid_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
        # The Python daemon exits before the frozen process finishes its
        # shutdown cleanup. Wait for that parent to release the copied EXE.
        for path in (copied, installed_exe):
            deadline = time.monotonic() + 8
            while path.exists():
                try:
                    path.unlink()
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)

    released = installer
    expected = "%s  %s\n" % (hashlib.sha256(released.read_bytes()).hexdigest(), released.name)
    assert (ROOT / "dist/SHA256SUMS.txt").read_text(encoding="utf-8") == expected
    print("Release checksum matches the EXE; installer checksum verified.")


if __name__ == "__main__":
    main()
