"""Own only the per-user program EXE; downloaded installers and data stay separate."""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import __version__
from .paths import frozen

EXE_NAME = "WuwaTappoPlus.exe"
RECEIPT_NAME = "install.json"


def directory():
    override = os.environ.get("WUWA_TAPPO_PLUS_PROGRAM_DIR")
    root = Path(override) if override else Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs" / "WuwaTappoPlus"
    if not root.is_absolute() or root.name != "WuwaTappoPlus":
        raise OSError("Plus 程序目录必须是绝对路径且目录名为 WuwaTappoPlus。")
    return root


def _validate(root):
    # Inspect original paths before resolve(): resolving first would hide links.
    for path in [*reversed(root.parents), root, root / EXE_NAME, root / RECEIPT_NAME]:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("为避免误操作，不使用链接指向的 Plus 程序目录：%s" % path)
    if root.exists() and not root.is_dir():
        raise OSError("Plus 程序目录被同名文件占用。")
    for path in [root / EXE_NAME, root / RECEIPT_NAME]:
        if path.exists() and not path.is_file():
            raise OSError("Plus 程序文件路径被目录占用：%s" % path)


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def deploy():
    """Copy before registering startup; never overwrite a mapped EXE in place."""
    if not frozen():
        return None
    root = directory()
    _validate(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / EXE_NAME
    source = Path(sys.executable).resolve()
    if source != target.resolve():
        fd, temporary = tempfile.mkstemp(prefix=".plus-install-", suffix=".exe", dir=root)
        os.close(fd)
        staged = Path(temporary)
        try:
            shutil.copyfile(source, staged)
            subprocess.run(["icacls", str(staged), "/setintegritylevel", "M"],
                           check=True, capture_output=True)
            deadline = time.monotonic() + 8
            while True:
                _validate(root)
                try:
                    staged.replace(target)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise OSError("请关闭已打开的 Plus 安装管理窗口后重试；原程序文件未被覆盖。")
                    time.sleep(0.2)
        finally:
            staged.unlink(missing_ok=True)
    receipt = {"generation": uuid.uuid4().hex, "version": __version__, "sha256": _digest(target)}
    # A new generation also cancels a delayed cleanup from an earlier uninstall.
    receipt_path = root / RECEIPT_NAME
    temporary = root / (".plus-install-" + uuid.uuid4().hex + ".json")
    try:
        temporary.write_text(json.dumps(receipt), encoding="utf-8")
        temporary.replace(receipt_path)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _cleanup_script(root, digest, receipt):
    config = json.dumps({"root": str(root), "exe": str(root / EXE_NAME),
                         "receipt": str(root / RECEIPT_NAME), "digest": digest, "generation": receipt})
    # Single-quote PowerShell literals, then encode the entire script. Never build
    # shell delete commands from a path or recursively remove the program folder.
    literal = "'" + config.replace("'", "''") + "'"
    return "$cfg = " + literal + " | ConvertFrom-Json;" + r"""
$ErrorActionPreference = 'Stop'
function Check-Paths {
    $dir = [IO.DirectoryInfo]::new($cfg.root)
    while ($null -ne $dir) {
        if ($dir.Exists -and (($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'Linked directory' }
        $dir = $dir.Parent
    }
    foreach ($path in @($cfg.exe, $cfg.receipt)) {
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path -Force
            if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'Linked file' }
        }
    }
}
$deadline = [DateTime]::UtcNow.AddMinutes(2)
while ([DateTime]::UtcNow -lt $deadline) {
    Check-Paths
    $record = if ([IO.File]::Exists($cfg.receipt)) { [IO.File]::ReadAllText($cfg.receipt) } else { '' }
    if ($record -cne $cfg.generation) { exit 0 }
    if (-not [IO.File]::Exists($cfg.exe)) { break }
    $stream = [IO.File]::OpenRead($cfg.exe)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
    if ($digest -ne $cfg.digest) { exit 0 }
    try { [IO.File]::Delete($cfg.exe); break } catch [IO.IOException] {} catch [UnauthorizedAccessException] {}
    Start-Sleep -Milliseconds 250
}
if ([IO.File]::Exists($cfg.exe)) { exit 1 }
Check-Paths
$record = if ([IO.File]::Exists($cfg.receipt)) { [IO.File]::ReadAllText($cfg.receipt) } else { '' }
if ($record -cne $cfg.generation) { exit 0 }
if ($cfg.generation) { [IO.File]::Delete($cfg.receipt) }
try { [IO.Directory]::Delete($cfg.root, $false) } catch [IO.IOException] {}
"""


def remove_program():
    """Return 'removed', 'deferred', or None for a source run."""
    if not frozen():
        return None
    root = directory()
    _validate(root)
    target, receipt_path = root / EXE_NAME, root / RECEIPT_NAME
    receipt = receipt_path.read_text(encoding="utf-8") if receipt_path.exists() else ""
    digest = _digest(target) if target.exists() else ""
    try:
        target.unlink(missing_ok=True)
    except PermissionError:
        script = _cleanup_script(root, digest, receipt)
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        subprocess.Popen([str(shell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                         cwd=str(root.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
        return "deferred"
    receipt_path.unlink(missing_ok=True)
    if root.exists() and not any(root.iterdir()):
        root.rmdir()
    return "removed"
