# Build with tools/build_release.py, which prepares metadata and license files.
from pathlib import Path

root = Path(SPECPATH).parent
analysis = Analysis(
    [str(root / "launch.py")],
    pathex=[str(root / "src")],
    datas=[(str(root / "src/wuwa_tappo_plus/scripts/*.js"), "wuwa_tappo_plus/scripts"),
           (str(root / "build/licenses"), "licenses")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="WuwaTappoPlus", debug=False, strip=False, upx=False, console=False,
    icon=str(root / "packaging/WuwaTappoPlus.ico"),
    manifest=str(root / "build/app.manifest"),
    version=str(root / "build/version-info.txt"),
)
