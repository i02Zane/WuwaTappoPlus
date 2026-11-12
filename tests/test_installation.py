"""Installer ownership and upgrade adoption, without touching the real install."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wuwa_tappo_plus import installation


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "WuwaTappoPlus"
        self.directory = patch.object(installation, "directory", return_value=self.root)
        self.directory.start()
        self.addCleanup(self.directory.stop)

    def test_source_operations_never_copy_or_delete_program_files(self):
        with patch.object(installation, "frozen", return_value=False):
            self.assertIsNone(installation.deploy())
            self.assertIsNone(installation.remove_program())
        self.assertFalse(self.root.exists())

    def test_upgrade_changes_legacy_generation_without_modifying_exe_or_unknown_files(self):
        self.root.mkdir()
        executable = self.root / installation.EXE_NAME
        executable.write_bytes(b"legacy executable")
        receipt = self.root / installation.RECEIPT_NAME
        receipt.write_text('{"generation":"old"}', encoding="utf-8")
        sentinel = self.root / "personal.txt"
        sentinel.write_text("keep")
        installation.record_installation(pending=True)
        record = json.loads(receipt.read_text())
        self.assertNotEqual(record["generation"], "old")
        self.assertTrue(record["pending"])
        self.assertEqual(executable.read_bytes(), b"legacy executable")
        self.assertEqual(sentinel.read_text(), "keep")
        with patch.object(installation, "frozen", return_value=True):
            self.assertFalse(installation.managed())

    def test_installed_receipt_and_cli_repair_use_installer_owned_program(self):
        self.root.mkdir()
        target = self.root / installation.EXE_NAME
        target.write_bytes(b"installed program")
        installation.record_installation()
        with patch.object(installation, "frozen", return_value=True), \
                patch.object(installation.sys, "executable", str(target)):
            self.assertTrue(installation.managed())
            self.assertEqual(installation.deploy(), target)

    def test_extracted_payload_cannot_self_install(self):
        self.root.mkdir()
        target = self.root / installation.EXE_NAME
        target.write_bytes(b"installed")
        installation.record_installation()
        with patch.object(installation, "frozen", return_value=True), \
                patch.object(installation.sys, "executable", str(self.root.parent / "download.exe")):
            with self.assertRaises(OSError):
                installation.deploy()
        self.assertEqual(target.read_bytes(), b"installed")

    def test_main_program_never_deletes_itself_or_spawns_a_cleanup_shell(self):
        self.root.mkdir()
        target = self.root / installation.EXE_NAME
        target.write_bytes(b"keep")
        with patch.object(installation, "frozen", return_value=True), \
                patch.object(installation.win, "popen_external") as spawn:
            with self.assertRaises(OSError):
                installation.remove_program()
        spawn.assert_not_called()
        self.assertEqual(target.read_bytes(), b"keep")

    def test_uninstaller_handoff_is_fixed_and_purge_must_be_explicit(self):
        self.root.mkdir()
        (self.root / installation.EXE_NAME).write_bytes(b"program")
        uninstaller = self.root / installation.UNINSTALLER_NAME
        uninstaller.write_bytes(b"uninstaller")
        installation.record_installation()
        with patch.object(installation, "frozen", return_value=True), \
                patch.object(installation.win, "popen_external") as spawn:
            installation.launch_uninstaller()
            self.assertEqual(spawn.call_args.args[0], [str(uninstaller)])
            installation.launch_uninstaller(purge=True, silent=True)
            self.assertIn("/PURGEDATA=yes", spawn.call_args.args[0])
            self.assertIn("/VERYSILENT", spawn.call_args.args[0])

    def test_repair_launches_setup_instead_of_elevating_the_main_program(self):
        self.root.mkdir()
        source = self.root.parent / "WuwaTappoPlus-Setup.exe"
        source.write_bytes(b"setup")
        installation.record_installation(setup_source=str(source))
        with patch.object(installation.win, "popen_external") as spawn:
            installation.launch_setup()
        self.assertEqual(spawn.call_args.args[0], [str(source)])

    def test_missing_setup_requires_redownload_without_changing_receipt(self):
        self.root.mkdir()
        installation.record_installation(setup_source=str(self.root.parent/'missing.exe'))
        receipt = (self.root/'install.json').read_bytes()
        with patch.object(installation.win, "popen_external") as spawn:
            with self.assertRaises(OSError):
                installation.launch_setup()
        spawn.assert_not_called()
        self.assertEqual((self.root/'install.json').read_bytes(), receipt)

    def test_links_in_existing_payload_abort_upgrade_before_writes(self):
        self.root.mkdir()
        outside = self.root.parent / "outside"
        outside.mkdir()
        linked = self.root / "_internal"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("directory link permission unavailable")
        with self.assertRaises(OSError):
            installation.record_installation()
        self.assertFalse((self.root / installation.RECEIPT_NAME).exists())
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
