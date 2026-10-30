"""Per-accessory display overrides; they only change this computer's rendering."""

import json
import os
import re
import tempfile
from pathlib import Path

from .config import CONFIG_PATH

FILE_NAME = "accessory_overrides.json"
FILE_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
LIMITS = {"x": (-500.0, 500.0), "y": (-500.0, 500.0),
          "scale": (0.2, 3.0), "rotation": (-180.0, 180.0)}


def default_path(config_dir=None):
    root = Path(config_dir) if config_dir is not None else CONFIG_PATH.parent
    return root / FILE_NAME


def _number(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("配件参数必须是数字")
    low, high = LIMITS[name]
    if value < low or value > high:
        raise ValueError("配件参数超出范围")
    return round(float(value), 2)


def normalize(file, x=0.0, y=0.0, scale=1.0, rotation=0.0):
    """Validate one override; an all-default entry means 'remove it'."""
    name = str(file or "").strip()
    if not FILE_KEY.match(name):
        raise ValueError("配件文件名无效")
    values = {"x": _number("x", x), "y": _number("y", y),
              "scale": _number("scale", scale), "rotation": _number("rotation", rotation)}
    if values == {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0}:
        return None
    return values


def _clean(entry):
    if not isinstance(entry, dict):
        return None
    try:
        return normalize(entry.get("file"), entry.get("x", 0), entry.get("y", 0),
                         entry.get("scale", 1), entry.get("rotation", 0))
    except (TypeError, ValueError):
        return None


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for file, entry in data.items():
        cleaned = _clean({**entry, "file": file} if isinstance(entry, dict) else None)
        if cleaned is not None:
            out[file] = cleaned
    return out


def save(path, overrides):
    """Atomically write a map of {accessory file: values}; never keep BOM."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(overrides, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".plus-accessories-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def set_override(path, file, x, y, scale, rotation):
    """Apply one change atomically and return the full, updated map."""
    path = Path(path)
    overrides = load(path)
    cleaned = normalize(file, x, y, scale, rotation)
    if cleaned is None:
        overrides.pop(file, None)
    else:
        overrides[file] = cleaned
    save(path, overrides)
    return overrides
