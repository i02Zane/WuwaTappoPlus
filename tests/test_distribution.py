"""Frozen/source command lines, data isolation, and setup result transport."""

import io
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus import __version__, cli, entry, paths, win
from wuwa_tappo_plus.config import Config


class PathTests(unittest.TestCase):
    def test_frozen_data_survives_moving_or_upgrading_the_executable(self):
        with patch.object(paths.sys, "frozen", True, create=True), \
                patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Example\AppData\Local"}, clear=True):
            with patch.object(paths.sys, "executable", r"D:\First\WuwaTappoPlus.exe"):
                first = paths.data_directory()
            with patch.object(paths.sys, "executable", r"D:\Upgrade\WuwaTappoPlus.exe"):
                self.assertEqual(paths.data_directory(), first)
            self.assertEqual(first.name, "WuwaTappoPlus")

    def test_source_install_keeps_its_existing_root(self):
        with patch.object(paths, "frozen", return_value=False), \
                patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.data_directory(), Path(__file__).resolve().parents[1])

    def test_relative_data_override_is_rejected(self):
        with patch.dict(os.environ, {"WUWA_TAPPO_PLUS_DATA_DIR": "relative"}):
            with self.assertRaises(ValueError):
                paths.data_directory()

    def test_installed_wheel_uses_user_data_instead_of_site_packages(self):
        with patch.object(paths, "frozen", return_value=False), \
                patch.object(paths, "source_root", return_value=None), \
                patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Example\AppData\Local"}, clear=True):
            self.assertEqual(paths.data_directory().name, "WuwaTappoPlus")


class CommandTests(unittest.TestCase):
    def test_independent_background_process_owns_its_onefile_extraction(self):
        with patch.object(win, "frozen", return_value=True), \
                patch.object(win.sys, "_MEIPASS", r"C:\Temp\_MEI123", create=True), \
                patch.object(win.subprocess, "Popen") as popen, \
                patch.dict(os.environ, {
                    "PATH": r"C:\Temp\_MEI123;C:\Windows\System32",
                    "PYINSTALLER_RESET_ENVIRONMENT": "original"}):
            win.launch_detached(["WuwaTappoPlus.exe", "run"])
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            self.assertNotIn("_MEI123", environment["PATH"])
            self.assertIn(r"C:\Windows\System32", environment["PATH"])
            self.assertEqual(os.environ["PYINSTALLER_RESET_ENVIRONMENT"], "original")

    def test_launch_app_does_not_inherit_the_onefile_path(self):
        with patch.object(win.sys, "_MEIPASS", r"C:\Temp\_MEI123", create=True), \
                patch.object(win.subprocess, "Popen") as popen, \
                patch.dict(os.environ, {
                    "PATH": r"C:\Temp\_MEI123;C:\Windows\System32",
                    "PYINSTALLER_RESET_ENVIRONMENT": "1"}):
            win.launch_app(r"C:\App\WuwaTappo.exe")
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(popen.call_args.args[0], [r"C:\App\WuwaTappo.exe"])
            self.assertNotIn("_MEI123", environment["PATH"])
            self.assertIn(r"C:\Windows\System32", environment["PATH"])
            self.assertNotIn("PYINSTALLER_RESET_ENVIRONMENT", environment)

    def test_elevated_launch_resets_extraction_and_restores_the_parent_environment(self):
        observed = []
        original_path = r"C:\Temp\_MEI123;C:\Windows\System32"
        with patch.object(win, "frozen", return_value=True), \
                patch.object(win.sys, "_MEIPASS", r"C:\Temp\_MEI123", create=True), \
                patch.object(win.ctypes.windll.shell32, "ShellExecuteW",
                             side_effect=lambda *args: observed.append(
                                 (os.environ.get("PYINSTALLER_RESET_ENVIRONMENT"),
                                  os.environ.get("PATH"))) or 42), \
                patch.dict(os.environ, {"PYINSTALLER_RESET_ENVIRONMENT": "original",
                                        "PATH": original_path}):
            self.assertTrue(win.relaunch_elevated(["install", "--elevated"]))
            self.assertEqual(observed[0][0], "1")
            self.assertNotIn("_MEI123", observed[0][1])
            self.assertEqual(os.environ["PYINSTALLER_RESET_ENVIRONMENT"], "original")
            self.assertEqual(os.environ["PATH"], original_path)

    def test_frozen_background_commands_do_not_invoke_python_or_a_source_launcher(self):
        executable = r"C:\Program Files\WuwaTappoPlus\WuwaTappoPlus.exe"
        with patch.object(win, "frozen", return_value=True), \
                patch.object(win.sys, "executable", executable):
            self.assertEqual(win.command_args("run", background=True), [executable, "run"])
            self.assertEqual(win.autostart_command(), '"%s" run --daemon' % executable)
            self.assertEqual(win.watchdog_command(), '"%s" ensure' % executable)
            self.assertEqual(win.pythonw(), executable)

    def test_source_background_command_has_an_absolute_cwd_independent_entry(self):
        with patch.object(win, "frozen", return_value=False), \
                patch.object(win, "pythonw", return_value="pythonw.exe"):
            command = win.command_args("ensure", background=True)
        self.assertEqual(command[0], "pythonw.exe")
        self.assertTrue(Path(command[1]).is_absolute())
        self.assertEqual(Path(command[1]).name, "launch.py")
        self.assertEqual(command[2:], ["ensure"])

    def test_installed_wheel_uses_module_entry_instead_of_a_nonexistent_launcher(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(win, "frozen", return_value=False), \
                patch.object(win, "APP_DIR", Path(directory)), \
                patch.object(win, "pythonw", return_value="pythonw.exe"):
            self.assertEqual(win.command_args("ensure", background=True),
                             ["pythonw.exe", "-m", "wuwa_tappo_plus", "ensure"])



class DllSearchTests(unittest.TestCase):
    def test_real_child_does_not_inherit_parent_dll_directory(self):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetDllDirectoryW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
        api.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        def current():
            buffer = ctypes.create_unicode_buffer(32768)
            return buffer.value if api.GetDllDirectoryW(len(buffer), buffer) else None
        child = (
            "import ctypes; a=ctypes.WinDLL('kernel32');"
            "a.GetDllDirectoryW.argtypes=[ctypes.c_uint,ctypes.c_wchar_p];"
            "b=ctypes.create_unicode_buffer(32768);"
            "a.GetDllDirectoryW(len(b),b);print(b.value)"
        )
        with tempfile.TemporaryDirectory() as directory, win._spawn_lock:
            previous = current()
            self.assertTrue(api.SetDllDirectoryW(directory))
            try:
                command = [sys.executable, "-c", child]
                inherited = subprocess.run(command, capture_output=True, text=True, check=True)
                self.assertEqual(inherited.stdout.strip(), directory)
                with patch.object(win, "frozen", return_value=True):
                    clean = win.run_external(command, capture_output=True, text=True, check=True)
                    self.assertEqual(current(), directory)
                    with self.assertRaises(OSError):
                        win.popen_external([str(Path(directory) / "missing.exe")])
                    self.assertEqual(current(), directory)
                self.assertEqual(clean.stdout.strip(), "")
            finally:
                api.SetDllDirectoryW(previous)

    def test_launch_and_uac_see_system_search_and_restore_afterwards(self):
        # Isolate API calls: neither launch a real app nor show a UAC prompt.
        directory = {"value": r"C:\Temp\_MEI-original"}
        def get_directory(size, buffer):
            buffer.value = directory["value"] or ""
            return len(buffer.value)
        def set_directory(value):
            directory["value"] = value
            return True
        api = Mock()
        api.GetDllDirectoryW.side_effect = get_directory
        api.SetDllDirectoryW.side_effect = set_directory
        observations = []
        def created(*args, **kwargs):
            observations.append(directory["value"])
            return 42
        with patch.object(win, "frozen", return_value=True), \
                patch.object(win.ctypes, "WinDLL", return_value=api), \
                patch.object(win.subprocess, "Popen", side_effect=created), \
                patch.object(win.ctypes.windll.shell32, "ShellExecuteW", side_effect=created):
            win.launch_app(r"C:\App\WuwaTappo.exe")
            win.launch_detached([r"C:\App\WuwaTappoPlus.exe", "run"])
            self.assertTrue(win.relaunch_elevated(["install", "--elevated"]))
        self.assertEqual(observations, [None, None, None])
        self.assertEqual(directory["value"], r"C:\Temp\_MEI-original")


class EntryTests(unittest.TestCase):
    def test_foreground_exe_opens_a_console(self):
        with patch.object(paths, "frozen", return_value=True), \
                patch("wuwa_tappo_plus.console.open_console") as console, \
                patch.object(cli, "main", return_value=0) as dispatch:
            code = entry.main(["status"])
        self.assertEqual(code, 0)
        console.assert_called_once_with()
        dispatch.assert_called_once_with(["status"])

    def test_uac_program_and_data_paths_are_consumed_before_dispatch(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {}, clear=False), \
                patch.object(paths, "frozen", return_value=False), \
                patch.object(cli, "main", return_value=0) as dispatch:
            program = str(Path(temp) / "Programs" / "WuwaTappoPlus")
            data = str(Path(temp) / "data")
            self.assertEqual(entry.main(["--data-dir", data, "--program-dir", program, "install"]), 0)
            self.assertEqual(os.environ["WUWA_TAPPO_PLUS_PROGRAM_DIR"], program)
            self.assertEqual(os.environ["WUWA_TAPPO_PLUS_DATA_DIR"], data)
            dispatch.assert_called_once_with(["install"])

    def test_background_entry_never_opens_a_console(self):
        for command in ("run", "ensure"):
            with patch.object(paths, "frozen", return_value=True), \
                    patch("wuwa_tappo_plus.console.open_console") as console, \
                    patch.object(cli, "main", return_value=0):
                self.assertEqual(entry.main([command]), 0)
                console.assert_not_called()

    def test_elevated_exe_uses_its_own_console(self):
        with patch.object(paths, "frozen", return_value=True), \
                patch("wuwa_tappo_plus.console.open_console") as console, \
                patch.object(cli, "main", return_value=0):
            self.assertEqual(entry.main(["install", "--elevated"]), 0)
        console.assert_called_once_with(force_new=True)

    def test_source_launcher_works_from_another_working_directory(self):
        launcher = Path(__file__).resolve().parents[1] / "launch.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(launcher), "help"], cwd=directory,
                                    capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("v" + __version__, result.stdout)

    def test_probe_is_read_only_and_checks_all_packaged_scripts(self):
        with patch.object(cli, "load") as load:
            result = entry.probe()
        load.assert_not_called()
        self.assertEqual(set(result["scripts"]), {"pet", "settings", "rooms", "toast", "game_mode", "plaza"})
        from wuwa_tappo_plus.script_source import script_files
        inventory = {path.name for path in paths.SCRIPTS_DIR.iterdir()
                     if path.suffix in (".js", ".json")}
        # A source checkout needs no Node build. These two generated resources
        # are mandatory in the release inventory, checked by smoke_release.py.
        generated = {"plaza-client.bundle.js", "plaza-config.json"}
        self.assertLessEqual(generated, set(script_files()))
        self.assertEqual(set(script_files()), inventory | generated)
