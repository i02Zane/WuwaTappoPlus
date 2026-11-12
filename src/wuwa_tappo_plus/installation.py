"""Installer ownership, safe upgrade receipts and native uninstaller handoff."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from . import __version__, win
from .paths import frozen

EXE_NAME = "WuwaTappoPlus.exe"
RECEIPT_NAME = "install.json"
UNINSTALLER_NAME = "unins000.exe"


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


def validate():
    root = directory()
    _validate(root)
    # Existing payload folders must not redirect an upgrade outside the app.
    if root.exists():
        for current, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(current) / name
                if path.is_symlink() or path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise OSError("程序目录包含链接，已中止操作：%s" % path)
    return root


def managed():
    if not frozen():
        return False
    root = validate()
    try:
        receipt = json.loads((root / RECEIPT_NAME).read_text(encoding="utf-8"))
        return receipt.get("manager") == "inno-setup" and not receipt.get("pending")
    except (OSError, ValueError, AttributeError):
        return False


def record_installation(pending=False, setup_source=None):
    root = validate()
    root.mkdir(parents=True, exist_ok=True)
    exe = root / EXE_NAME
    receipt = {"manager": "inno-setup", "generation": uuid.uuid4().hex,
               "version": __version__, "pending": pending}
    if setup_source:
        source = Path(setup_source)
        if not source.is_absolute():
            raise ValueError("安装包来源必须是绝对路径。")
        receipt["setup_source"] = str(source)
    if exe.is_file():
        with exe.open("rb") as stream:
            receipt["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    # Changing the generation cancels an old version's delayed cleanup helper.
    temporary = root / (".plus-install-" + uuid.uuid4().hex + ".json")
    try:
        temporary.write_text(json.dumps(receipt), encoding="utf-8")
        temporary.replace(root / RECEIPT_NAME)
    finally:
        temporary.unlink(missing_ok=True)
    return exe


def deploy():
    """File deployment belongs to Setup; CLI install only repairs its own app."""
    if not frozen():
        return None
    target = validate() / EXE_NAME
    if Path(sys.executable).resolve() != target.resolve() or not managed():
        raise OSError("请运行 WuwaTappoPlus-Setup.exe 安装或更新 Plus。")
    return target


def launch_uninstaller(purge=False, silent=False):
    root = validate()
    uninstaller = root / UNINSTALLER_NAME
    if not managed() or not uninstaller.is_file():
        raise OSError("未找到安装器的卸载程序，请重新运行安装包修复。")
    command = [str(uninstaller)]
    if purge:
        command.append("/PURGEDATA=yes")
    if silent:
        command.extend(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
    win.popen_external(command, cwd=str(root), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def launch_setup():
    root = validate()
    try:
        receipt = json.loads((root / RECEIPT_NAME).read_text(encoding="utf-8"))
        source = Path(receipt.get("setup_source", ""))
    except (OSError, ValueError, TypeError) as exc:
        raise OSError("请重新下载并运行 WuwaTappoPlus-Setup.exe 修复安装。") from exc
    if not source.is_absolute() or not source.is_file() or source.suffix.lower() != ".exe":
        raise OSError("原安装包已移动，请重新下载并运行 WuwaTappoPlus-Setup.exe 修复安装。")
    win.popen_external([str(source)], cwd=str(source.parent),
                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def remove_program():
    if not frozen():
        return None
    validate()
    raise OSError("程序文件由安装器管理，请从 Windows 已安装的应用中卸载 Plus。")
