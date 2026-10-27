"""Build a standalone Windows EXE and its SHA-256 checksum."""

import hashlib
import platform
import shutil
import subprocess
import struct
import sys
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wuwa_tappo_plus import __version__


def validate_build_environment():
    if (sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64")
            or struct.calcsize("P") != 8 or platform.python_implementation() != "CPython"):
        raise SystemExit("Build this Windows x64 release using 64-bit Python on Windows.")


def validate_executable(executable, build):
    """Reject incomplete bundles before emitting a checksum."""
    import json
    import os

    check = build / "release-check"
    check.mkdir(parents=True, exist_ok=True)
    report = check / "startup.json"
    report.unlink(missing_ok=True)
    result = subprocess.run(
        [str(executable), "--output", str(report), "--probe"], cwd=check,
        env={**os.environ, "WUWA_TAPPO_PLUS_DATA_DIR": str(check),
             "PYINSTALLER_RESET_ENVIRONMENT": "1"}, timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Packaged EXE failed startup verification; see %s" % report)
    payload = json.loads(report.read_text(encoding="utf-8"))
    if (not payload.get("frozen") or payload.get("version") != __version__
            or payload.get("scripts") != dict.fromkeys(("pet", "settings", "rooms"), __version__)
            or len(payload.get("licenses", [])) != 6):
        raise RuntimeError("Packaged EXE returned inconsistent release metadata; see %s" % report)


def main():
    validate_build_environment()
    build = ROOT / "build"
    licenses = build / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    for notice in (ROOT / "packaging/licenses").glob("*.txt"):
        shutil.copy2(notice, licenses / notice.name)
    shutil.copy2(ROOT / "LICENSE", licenses / "WuwaTappoPlus.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", licenses / "Third-party-notices.md")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python.txt")
    pyinstaller = distribution("pyinstaller")
    copying = next(p for p in pyinstaller.files if str(p).endswith("licenses/COPYING.txt"))
    shutil.copy2(pyinstaller.locate_file(copying), licenses / "PyInstaller.txt")
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
    )
    version = tuple(map(int, __version__.split("."))) + (0,)
    manifest = (ROOT / "packaging/app.manifest").read_text(encoding="utf-8")
    (build / "app.manifest").write_text(
        manifest.replace("__VERSION__", ".".join(map(str, version))), encoding="utf-8")
    metadata = VSVersionInfo(ffi=FixedFileInfo(filevers=version, prodvers=version), kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Wuwa Tappo Plus contributors"),
            StringStruct("FileDescription", "Wuwa Tappo Plus"),
            StringStruct("FileVersion", __version__),
            StringStruct("ProductName", "Wuwa Tappo Plus"),
            StringStruct("ProductVersion", __version__),
            StringStruct("OriginalFilename", "WuwaTappoPlus.exe"),
            StringStruct("LegalCopyright", "Wuwa Tappo Plus contributors; GPL-3.0-only"),
        ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ])
    (build / "version-info.txt").write_text(str(metadata), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(build / "pyinstaller"),
                    str(ROOT / "packaging/WuwaTappoPlus.spec")], cwd=ROOT, check=True)
    executable = ROOT / "dist/WuwaTappoPlus.exe"
    validate_executable(executable, build)
    checksum = ROOT / "dist/SHA256SUMS.txt"
    checksum.write_text("%s  %s\n" %
                        (hashlib.sha256(executable.read_bytes()).hexdigest(), executable.name),
                        encoding="utf-8")
    print("\nBuilt:", executable, "\nChecksum:", checksum)


if __name__ == "__main__":
    main()
