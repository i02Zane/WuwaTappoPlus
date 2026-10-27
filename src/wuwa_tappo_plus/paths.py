"""Separate bundled resources, launch paths and persistent user data."""

import os
import sys
from pathlib import Path


def frozen():
    return bool(getattr(sys, "frozen", False))


def source_root():
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "launch.py").is_file() else None


def app_directory():
    if frozen():
        return Path(sys.executable).resolve().parent
    return source_root() or Path(sys.executable).resolve().parent


def data_directory():
    override = os.environ.get("WUWA_TAPPO_PLUS_DATA_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError("WUWA_TAPPO_PLUS_DATA_DIR must be an absolute path")
        return path
    if frozen() or source_root() is None:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "WuwaTappoPlus"
    # Development runs keep their data in the checkout.
    return app_directory()


APP_DIR = app_directory()
DATA_DIR = data_directory()
SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
