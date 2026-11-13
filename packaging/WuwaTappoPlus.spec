# Build with tools/build_release.py, which prepares metadata and license files.
from pathlib import Path

root = Path(SPECPATH).parent
analysis = Analysis(
    [str(root / "launch.py")],
    pathex=[str(root / "src")],
    datas=[(str(root / "src/wuwa_tappo_plus/scripts/*.js"), "wuwa_tappo_plus/scripts"),
           (str(root / "src/wuwa_tappo_plus/scripts/*.json"), "wuwa_tappo_plus/scripts"),
           (str(root / "build/licenses"), "licenses")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
# Windows 10/11 supply UCRT as an OS component. Do not redistribute a copy
# discovered in a build machine's third-party tool directories.
analysis.binaries = [entry for entry in analysis.binaries
                     if Path(entry[0]).name.lower() != "ucrtbase.dll"]
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name="WuwaTappoPlus", debug=False, strip=False, upx=False, console=False,
    icon=str(root / "packaging/WuwaTappoPlus.ico"),
    manifest=str(root / "build/app.manifest"),
    version=str(root / "build/version-info.txt"),
)
collection = COLLECT(executable, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name="WuwaTappoPlus")
