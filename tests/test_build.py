"""Release builds must reject wrong architectures and incomplete executables."""

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_builder", ROOT / "tools/build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseBuildTests(unittest.TestCase):
    def test_32_bit_python_on_a_64_bit_host_cannot_emit_an_x64_release(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(builder, "ROOT", Path(directory)), \
                patch.object(builder.sys, "platform", "win32"), \
                patch.object(builder.platform, "machine", return_value="AMD64"), \
                patch.object(builder.struct, "calcsize", return_value=4):
            with self.assertRaises(SystemExit):
                builder.main()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_failed_frozen_startup_is_rejected_and_keeps_its_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)

            def fail(command, **kwargs):
                Path(command[2]).write_text("missing SSL runtime data", encoding="utf-8")
                return subprocess.CompletedProcess(command, 1)

            with patch.object(builder.subprocess, "run", side_effect=fail):
                with self.assertRaises(RuntimeError):
                    builder.validate_executable(build / "program.exe", build)
            self.assertEqual((build / "release-check/startup.json").read_text(),
                             "missing SSL runtime data")

    def test_wrong_version_is_rejected_even_if_the_executable_exits_successfully(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)

            def wrong_version(command, **kwargs):
                Path(command[2]).write_text(json.dumps({"frozen": True, "version": "0.0.0"}),
                                            encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            with patch.object(builder.subprocess, "run", side_effect=wrong_version):
                with self.assertRaises(RuntimeError):
                    builder.validate_executable(build / "program.exe", build)


class ReleaseArchiveTests(unittest.TestCase):
    def prepare(self, root):
        dist = root / "dist"
        dist.mkdir()
        executable = dist / "WuwaTappoPlus.exe"
        executable.write_bytes(b"release fixture")
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        (dist / "SHA256SUMS.txt").write_text(digest + "  WuwaTappoPlus.exe\n", encoding="utf-8")
        notes = root / "notes.md"
        notes.write_text("# Release notes\n", encoding="utf-8")
        return notes

    def test_archive_contains_exe_checksum_and_release_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notes = self.prepare(root)
            with patch.object(builder, "ROOT", root):
                archived = builder.archive_release(notes)
            self.assertEqual(archived, root / "dist/history" / ("v" + builder.__version__))
            self.assertEqual({p.name for p in archived.iterdir()},
                             {"WuwaTappoPlus.exe", "SHA256SUMS.txt", "release-notes.md",
                              "WuwaTappoPlus-%s-windows-x64.zip" % builder.__version__})
            self.assertEqual((archived / "WuwaTappoPlus.exe").read_bytes(), b"release fixture")
            self.assertEqual((archived / "release-notes.md").read_text(encoding="utf-8"),
                             notes.read_text(encoding="utf-8"))
            self.assertEqual(list((root / "dist/history").iterdir()), [archived])
            share_zip = archived / ("WuwaTappoPlus-%s-windows-x64.zip" % builder.__version__)
            with zipfile.ZipFile(share_zip) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(set(archive.namelist()),
                                 {"WuwaTappoPlus.exe", "SHA256SUMS.txt", "release-notes.md"})
                for name in archive.namelist():
                    self.assertEqual(archive.read(name), (archived / name).read_bytes())
            before = share_zip.read_bytes()
            with self.assertRaises(FileExistsError):
                builder.create_share_zip(archived)
            self.assertEqual(share_zip.read_bytes(), before)

    def test_existing_history_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notes = self.prepare(root)
            with patch.object(builder, "ROOT", root):
                archived = builder.archive_release(notes)
                notes.write_text("changed", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    builder.archive_release(notes)
            self.assertEqual((archived / "release-notes.md").read_text(encoding="utf-8"),
                             "# Release notes\n")

    def test_checksum_mismatch_prevents_archiving(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notes = self.prepare(root)
            (root / "dist/WuwaTappoPlus.exe").write_bytes(b"tampered")
            with patch.object(builder, "ROOT", root):
                with self.assertRaises(ValueError):
                    builder.archive_release(notes)
            self.assertFalse((root / "dist/history").exists())
