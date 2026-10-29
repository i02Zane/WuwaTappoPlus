"""Program deployment and bounded cleanup, isolated from the real installation."""

import ctypes
import os
import subprocess
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus import installation, win


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Programs" / "WuwaTappoPlus"
        self.env = patch.dict(os.environ, {"WUWA_TAPPO_PLUS_PROGRAM_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_source_operations_do_not_copy_or_delete_a_program(self):
        with patch.object(installation, "frozen", return_value=False):
            self.assertIsNone(installation.deploy())
            self.assertIsNone(installation.remove_program())
        self.assertFalse(self.root.exists())

    def test_install_copies_atomically_and_routes_startup_to_the_installed_exe(self):
        source = self.base / "download.exe"
        source.write_bytes(b"new program")
        with patch.object(installation, "frozen", return_value=True), \
                patch.object(installation.sys, "executable", str(source)), \
                patch.object(installation.subprocess, "run"):
            target = installation.deploy()
            self.assertEqual(target, self.root / installation.EXE_NAME)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            first = (self.root / installation.RECEIPT_NAME).read_text()
            installation.deploy()
            self.assertNotEqual((self.root / installation.RECEIPT_NAME).read_text(), first)
            self.assertEqual(list(self.root.glob(".plus-install-*")), [])
        with patch.object(win, "frozen", return_value=True), patch.object(win, "_launch_executable", target):
            self.assertEqual(win.command_args("run")[0], str(target))
            self.assertIn(str(target), win.autostart_command())
            self.assertIn(str(target), win.watchdog_command())
        self.assertTrue(source.exists())

    def test_failed_replacement_keeps_the_previous_program_and_cleans_staging(self):
        self.root.mkdir(parents=True)
        target = self.root / installation.EXE_NAME
        target.write_bytes(b"old program")
        source = self.base / "download.exe"
        source.write_bytes(b"new program")
        with patch.object(installation, "frozen", return_value=True), \
                patch.object(installation.sys, "executable", str(source)), \
                patch.object(installation.subprocess, "run"), \
                patch.object(Path, "replace", side_effect=PermissionError("locked")), \
                patch.object(installation.time, "monotonic", side_effect=[0, 10]):
            with self.assertRaises(OSError):
                installation.deploy()
        self.assertEqual(target.read_bytes(), b"old program")
        self.assertEqual(list(self.root.glob(".plus-install-*")), [])

    def test_uninstall_removes_only_owned_program_files_and_keeps_download_and_data(self):
        self.root.mkdir(parents=True)
        (self.root / installation.EXE_NAME).write_bytes(b"program")
        (self.root / installation.RECEIPT_NAME).write_text("receipt")
        sentinel = self.root / "notes.txt"
        sentinel.write_text("keep")
        downloaded = self.base / "download.exe"
        downloaded.write_bytes(b"keep")
        data = self.base / "data"
        data.mkdir()
        (data / "config.toml").write_text("keep")
        with patch.object(installation, "frozen", return_value=True):
            self.assertEqual(installation.remove_program(), "removed")
        self.assertEqual(list(self.root.iterdir()), [sentinel])
        self.assertTrue(downloaded.exists())
        self.assertTrue((data / "config.toml").exists())

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
    def test_delayed_helper_removes_a_locked_exe_after_release(self):
        self._check_delayed_cleanup(reinstalled=False)

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
    def test_reinstall_cancels_pending_cleanup(self):
        self._check_delayed_cleanup(reinstalled=True)

    def _check_delayed_cleanup(self, reinstalled):
        self.root.mkdir(parents=True)
        target = self.root / installation.EXE_NAME
        target.write_bytes(b"program")
        record = self.root / installation.RECEIPT_NAME
        record.write_text("generation one", encoding="utf-8")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        real_popen = subprocess.Popen
        processes = []
        def launch(*args, **kwargs):
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        try:
            with patch.object(installation, "frozen", return_value=True), \
                    patch.object(installation.subprocess, "Popen", side_effect=launch):
                self.assertEqual(installation.remove_program(), "deferred")
            self.assertTrue(target.exists())
            if reinstalled:
                record.write_text("generation two", encoding="utf-8")
        finally:
            kernel.CloseHandle(handle)
        try:
            stdout, stderr = processes[0].communicate(timeout=12)
            self.assertEqual(processes[0].returncode, 0, stderr.decode(errors="replace"))
        finally:
            if processes[0].poll() is None:
                processes[0].terminate()
                processes[0].wait(timeout=5)
        self.assertEqual(target.exists(), reinstalled, stdout.decode(errors="replace") + stderr.decode(errors="replace"))
        self.assertEqual(record.exists(), reinstalled)

    @unittest.skipUnless(os.name == "nt", "Windows junctions")
    def test_junctioned_program_directory_is_rejected_before_any_deletion(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / installation.EXE_NAME
        sentinel.write_bytes(b"external")
        self.root.parent.mkdir()
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 "New-Item -ItemType Junction -Path $env:WTP_PROGRAM_LINK "
                                 "-Target $env:WTP_PROGRAM_OUTSIDE -ErrorAction Stop | Out-Null"],
                                env={**os.environ, "WTP_PROGRAM_LINK": str(self.root),
                                     "WTP_PROGRAM_OUTSIDE": str(outside)}, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with patch.object(installation, "frozen", return_value=True):
                with self.assertRaises(OSError):
                    installation.remove_program()
            self.assertEqual(sentinel.read_bytes(), b"external")
        finally:
            self.root.rmdir()  # Only the junction entry, never its target.

    def test_symlinked_program_path_is_rejected_before_deletion(self):
        self.root.mkdir(parents=True)
        outside = self.base / "keep.exe"
        outside.write_bytes(b"external")
        linked = self.root / installation.EXE_NAME
        try:
            linked.symlink_to(outside)
        except OSError:
            self.skipTest("symlink permission unavailable")
        with patch.object(installation, "frozen", return_value=True):
            with self.assertRaises(OSError):
                installation.remove_program()
        self.assertEqual(outside.read_bytes(), b"external")


if __name__ == "__main__":
    unittest.main()
