"""Build the directory payload and the Inno Setup installer with its checksum."""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import struct
import sys
import tempfile
import zipfile
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wuwa_tappo_plus import __version__

INSTALLER_NAME = "WuwaTappoPlus-Setup.exe"


def find_iscc():
    override = os.environ.get("WUWA_INNO_COMPILER")
    candidates = [Path(override)] if override else []
    located = shutil.which("ISCC.exe")
    if located:
        candidates.append(Path(located))
    candidates.append(ROOT / "build/inno-compiler/ISCC.exe")
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        directory = os.environ.get(variable)
        if directory:
            for name in ("Inno Setup 7", "Inno Setup 6", "Programs/Inno Setup 6"):
                candidates.append(Path(directory) / name / "ISCC.exe")
    for candidate in candidates:
        if candidate.is_absolute() and candidate.is_file():
            return candidate
    raise SystemExit("Install Inno Setup 6.5+ or set WUWA_INNO_COMPILER to the absolute ISCC.exe path.")


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
            or payload.get("scripts") != dict.fromkeys(
                ("pet", "settings", "rooms", "toast", "game_mode", "plaza"), __version__)
            or len(payload.get("licenses", [])) != 7):
        raise RuntimeError("Packaged EXE returned inconsistent release metadata; see %s" % report)


def validate_installer_version(executable, reader=None):
    """AppVersion alone does not populate Setup's binary version resource."""
    if reader is None:
        from PyInstaller.utils.win32.versioninfo import read_version_info_from_executable

        reader = read_version_info_from_executable
    fixed = reader(str(executable)).ffi
    major, minor, patch = map(int, __version__.split("."))
    expected = (major << 16 | minor, patch << 16)
    if ((fixed.fileVersionMS, fixed.fileVersionLS) != expected
            or (fixed.productVersionMS, fixed.productVersionLS) != expected):
        raise RuntimeError("Installer file/product version does not match %s" % __version__)


def create_share_zip(directory):
    """Bundle only the published artifacts for local cloud-drive sharing."""
    directory = Path(directory)
    names = (INSTALLER_NAME, "SHA256SUMS.txt", "release-notes.md")
    for name in names:
        if not (directory / name).is_file():
            raise FileNotFoundError(directory / name)
    executable = directory / names[0]
    expected = "%s  %s" % (hashlib.sha256(executable.read_bytes()).hexdigest(), executable.name)
    if (directory / names[1]).read_text(encoding="utf-8").strip() != expected:
        raise ValueError("EXE checksum mismatch; sharing ZIP was not created")
    target = directory / ("WuwaTappoPlus-%s-windows-x64.zip" % __version__)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(directory / name, arcname=name)
    return target


def archive_release(notes_path):
    """Snapshot published files without overwriting an existing version."""
    notes = Path(notes_path)
    executable = ROOT / "dist" / INSTALLER_NAME
    checksum = ROOT / "dist/SHA256SUMS.txt"
    if not notes.is_file():
        raise FileNotFoundError("Release notes not found: %s" % notes)
    expected = "%s  %s" % (hashlib.sha256(executable.read_bytes()).hexdigest(), executable.name)
    if checksum.read_text(encoding="utf-8").strip() != expected:
        raise ValueError("EXE checksum mismatch; release was not archived")
    history = ROOT / "dist/history"
    destination = history / ("v" + __version__)
    if destination.exists():
        raise FileExistsError("Release history already exists: %s" % destination)
    history.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archive-", dir=history) as temporary:
        staged = Path(temporary)
        shutil.copy2(executable, staged / executable.name)
        if sys.platform == "win32":
            subprocess.run(["icacls", str(staged / executable.name), "/setintegritylevel", "M"],
                           check=True, capture_output=True)
        shutil.copy2(checksum, staged / checksum.name)
        shutil.copy2(notes, staged / "release-notes.md")
        create_share_zip(staged)
        staged.rename(destination)
    return destination


def main(argv=None):
    validate_build_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=Path,
                        help="archive this release with its notes under dist/history/vVERSION")
    args = parser.parse_args(argv)
    if args.notes is not None and not args.notes.is_file():
        parser.error("release notes file does not exist: %s" % args.notes)
    compiler = find_iscc()
    subprocess.run(["npm.cmd", "run", "build:plaza"], cwd=ROOT, check=True)
    build = ROOT / "build"
    licenses = build / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    (licenses / "JavaScript-components.txt").unlink(missing_ok=True)
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
                    "--distpath", str(build / "payload"), "--workpath", str(build / "pyinstaller"),
                    str(ROOT / "packaging/WuwaTappoPlus.spec")], cwd=ROOT, check=True)
    executable = build / "payload/WuwaTappoPlus/WuwaTappoPlus.exe"
    # A workspace may carry an inheritable Low integrity label. Shipping that
    # label makes the one-file bootloader unable to use a normal user's TEMP.
    # Set an explicit Medium label before probing or emitting the checksum.
    subprocess.run(["icacls", str(executable), "/setintegritylevel", "M"],
                   check=True, capture_output=True)
    validate_executable(executable, build)
    subprocess.run([str(compiler), "/DAppVersion=" + __version__,
                    str(ROOT / "packaging/setup.iss")], cwd=ROOT, check=True)
    executable = ROOT / "dist" / INSTALLER_NAME
    subprocess.run(["icacls", str(executable), "/setintegritylevel", "M"],
                   check=True, capture_output=True)
    validate_installer_version(executable)
    checksum = ROOT / "dist/SHA256SUMS.txt"
    checksum.write_text("%s  %s\n" %
                        (hashlib.sha256(executable.read_bytes()).hexdigest(), executable.name),
                        encoding="utf-8")
    print("\nBuilt:", executable, "\nChecksum:", checksum)
    if args.notes is not None:
        print("History:", archive_release(args.notes))


if __name__ == "__main__":
    main()
