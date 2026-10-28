"""Export bounded Plus logs and a small, whitelisted diagnostic summary."""

import json
import os
import re
import stat
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .logs import MAX_ENTRY_BYTES
from .paths import DATA_DIR


def _regular(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


NATIVE_FILE_LIMIT = 2 * 1024 * 1024
NATIVE_TOTAL_LIMIT = 8 * 1024 * 1024
NATIVE_FILE_COUNT = 8


def _native_log_directory():
    local = os.environ.get("LOCALAPPDATA")
    if not local or not Path(local).is_absolute():
        return None
    return Path(local) / "com.kuro.wuwatappo.cn" / "logs"


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _export_native_logs(archive):
    result = {"status": "missing", "logs": [], "skipped": [],
              "limits": {"files": NATIVE_FILE_COUNT, "file_bytes": NATIVE_FILE_LIMIT,
                         "total_bytes": NATIVE_TOTAL_LIMIT}}
    directory = _native_log_directory()
    if directory is None:
        return result
    try:
        if _linked(directory.parent) or _linked(directory):
            result["status"] = "linked-directory"
            return result
        candidates = []
        for path in directory.iterdir():
            # Direct log files only; never recurse into WebView or account data.
            if not re.fullmatch(r".+\.log(?:\.[0-9]+)?", path.name, re.IGNORECASE):
                continue
            try:
                if _regular(path):
                    candidates.append((path.stat().st_mtime_ns, path.name, path))
            except OSError:
                result["skipped"].append(path.name)
    except FileNotFoundError:
        return result
    except OSError:
        result["status"] = "unreadable"
        return result
    remaining = NATIVE_TOTAL_LIMIT
    for _, name, path in sorted(candidates, reverse=True)[:NATIVE_FILE_COUNT]:
        if not remaining:
            break
        try:
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                limit = min(NATIVE_FILE_LIMIT, remaining)
                stream.seek(max(0, size - limit))
                content = stream.read(limit)
        except OSError:
            result["skipped"].append(name)
            continue
        archive.writestr("wuwa-tappo/" + name, content)
        remaining -= len(content)
        result["logs"].append({"name": name, "bytes": len(content), "truncated": size > limit})
    result["status"] = "included" if result["logs"] else "empty"
    if result["skipped"]:
        result["status"] = "partial" if result["logs"] else "unreadable"
    return result


def export_logs(cfg, destination=None, data_dir=None):
    """Create a new ZIP without touching source logs or copying config/databases."""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    logs = root / "logs"
    if logs.is_symlink() or (logs.exists() and getattr(logs.lstat(), "st_file_attributes", 0)
                             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise OSError("日志目录是链接，未导出。")
    now = datetime.now()
    if destination is None:
        from .win import desktop_directory
        destination = desktop_directory() / ("WuwaTappoPlus-logs-%s-%s.zip" % (
            now.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6]))
    destination = Path(destination)
    if destination.suffix.lower() != ".zip":
        raise ValueError("日志导出文件必须使用 .zip 后缀。")
    max_bytes = max(64 * 1024, cfg.log_max_bytes)
    keep = max(1, cfg.log_keep)
    summary = {"plus_version": __version__, "exported_at": now.isoformat(timespec="seconds"),
               "preferences": cfg.preferences,
               "log_limits": {"max_bytes": max_bytes, "backups": keep,
                              "total_bytes": max_bytes * (keep + 1)},
               "state": {}, "logs": []}
    state = root / ".state.json"
    if _regular(state):
        try:
            with state.open("rb") as stream:
                payload = json.loads(stream.read(64 * 1024))
            if isinstance(payload, dict):
                for key in ("plus_version", "updated", "started", "auto_open", "balance",
                            "remain_left", "remain_right", "inject", "alert", "port"):
                    value = payload.get(key)
                    if value is None or type(value) in (str, int, float, bool):
                        summary["state"][key] = value
        except (OSError, ValueError):
            pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("xb") as output:
            created = True
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                names = ["auto.log.%d" % i for i in range(keep, 0, -1)] + ["auto.log"]
                for name in names:
                    path = logs / name
                    if not _regular(path):
                        continue
                    try:
                        with path.open("rb") as stream:
                            size = os.fstat(stream.fileno()).st_size
                            # Permit one oversized entry from older releases;
                            # never read an unbounded file into an export.
                            limit = max_bytes + MAX_ENTRY_BYTES
                            stream.seek(max(0, size - limit))
                            content = stream.read(limit)
                    except FileNotFoundError:  # The daemon may rotate during export.
                        continue
                    archive.writestr("logs/" + name, content)
                    summary["logs"].append({"name": name, "bytes": len(content),
                                            "truncated": size > limit})
                summary["wuwa_tappo"] = _export_native_logs(archive)
                archive.writestr("diagnostics.json", json.dumps(summary, ensure_ascii=False, indent=2))
                archive.writestr("说明.txt", "Wuwa Tappo Plus 日志导出\n"
                    "包含 Plus 当前及轮转日志、Wuwa Tappo 原生日志和诊断摘要。\n"
                    "不复制原始配置、登录凭据文件、WebView 数据或统计数据库。\n"
                    "后台可继续运行，导出不会删除或清空日志。日志原文可能包含账户标识、余额、路径和提示文字，分享前请检查。\n"
                    "原生日志最多最近 8 个，每个末尾 2 MiB，总计不超过 8 MiB；缺失或不可读会在摘要注明。\n"
                    "logs 列表为空表示当前没有可用 Plus 日志；超大历史文件只保留末尾内容。\n")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return destination
