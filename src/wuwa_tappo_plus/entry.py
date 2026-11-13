"""Shared terminal dispatch for source and packaged Windows execution."""

import contextlib
import json
import os
import sys
import traceback
from pathlib import Path


def probe():
    """Read-only packaging smoke check; never modifies the installed desktop pet."""
    import sqlite3
    import ssl
    from . import __version__, game_mode
    from .config import Config
    from .paths import APP_DIR, DATA_DIR, SCRIPTS_DIR, frozen
    from .rooms import RoomBridge
    from .settings import SettingsBridge
    from .pet_page import inject_source
    from .toasts import ToastBridge
    from .plaza import PlazaBridge

    class QuietLog:
        pass

    sources = {"game_mode": game_mode.source(), "pet": inject_source(Config({})),
               "settings": SettingsBridge(9222, "127.0.0.1", QuietLog()).source,
               "rooms": RoomBridge(9222, "127.0.0.1", QuietLog()).source,
               "toast": ToastBridge(9222, "127.0.0.1", QuietLog()).source,
               "plaza": PlazaBridge(QuietLog()).source}
    if any("__VERSION__" in source or "__CFG__" in source for source in sources.values()):
        raise RuntimeError("unrendered script template")
    licenses = []
    if frozen():
        directory = Path(sys._MEIPASS) / "licenses"
        if (Path(sys._MEIPASS) / "ucrtbase.dll").exists():
            raise RuntimeError("UCRT must come from Windows, not the application bundle")
        licenses = ["WuwaTappoPlus.txt", "Python.txt", "Python-components.txt",
                    "OpenSSL.txt", "PyInstaller.txt", "Third-party-notices.md", "InnoSetup.txt"]
        if not all((directory / name).is_file() for name in licenses):
            raise RuntimeError("packaged license notice is missing")
    # Exercise the native libraries without opening databases on disk or
    # connecting to the network, including the first TLS handshake operation.
    with contextlib.closing(sqlite3.connect(":memory:")) as database:
        if database.execute("select 1 + 1").fetchone() != (2,):
            raise RuntimeError("SQLite startup check failed")
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).wrap_bio(
        incoming, outgoing, server_side=False, server_hostname="localhost")
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass  # A ClientHello was generated; no network peer is needed here.
    if not outgoing.pending:
        raise RuntimeError("TLS startup check failed")
    return {"version": __version__, "frozen": frozen(), "app_dir": str(APP_DIR),
            "data_dir": str(DATA_DIR), "scripts_dir": str(SCRIPTS_DIR),
            "scripts": {name: __version__ for name in sources}, "licenses": licenses}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Keep the original data folder across UAC, even with a different admin account.
    while argv[:1] in (["--data-dir"], ["--program-dir"]):
        if len(argv) < 3 or not Path(argv[1]).is_absolute():
            return 2
        key = "WUWA_TAPPO_PLUS_DATA_DIR" if argv[0] == "--data-dir" else "WUWA_TAPPO_PLUS_PROGRAM_DIR"
        os.environ[key] = argv[1]
        argv = argv[2:]

    from .paths import frozen

    destination = None
    if argv[:1] == ["--output"]:
        if len(argv) < 3:
            return 2
        destination = Path(argv[1])
        argv = argv[2:]
    background = bool(argv and argv[0] in ("run", "ensure", "--probe", "--installer"))
    if frozen() and not background and destination is None:
        from .console import open_console
        if "--elevated" in argv:
            open_console(force_new=True)
        else:
            open_console()
    if argv[:1] == ["--console-smoke"]:
        if len(argv) != 2:
            return 2
        from .console import console_info
        from .cli import main as cli_main
        code = cli_main(["menu", "--once"])
        Path(argv[1]).write_text(json.dumps(console_info()), encoding="utf-8")
        return code

    with contextlib.ExitStack() as stack:
        stream = None
        if destination is not None:
            stream = stack.enter_context(destination.open("w", encoding="utf-8"))
        elif sys.stdout is None:
            stream = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
        if stream:
            stack.enter_context(contextlib.redirect_stdout(stream))
            stack.enter_context(contextlib.redirect_stderr(stream))
        try:
            if argv[:1] == ["--installer"]:
                from .installer import main as installer_main

                return installer_main(argv[1:])
            if argv == ["--probe"]:
                print(json.dumps(probe(), ensure_ascii=False))
                return 0
            from .cli import main as cli_main
            code = cli_main(argv)
            if code and "--elevated" in argv and sys.stdin and sys.stdin.isatty():
                input("\n按 Enter 键关闭此窗口...")
            return code
        except Exception:
            traceback.print_exc()
            return 1
