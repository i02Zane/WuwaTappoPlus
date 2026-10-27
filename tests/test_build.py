"""Release builds must reject wrong architectures and incomplete executables."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
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
