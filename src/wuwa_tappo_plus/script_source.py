"""Assemble local page modules into one injection, without a runtime JS loader."""

import json

from .paths import SCRIPTS_DIR

MODULE_MARKER = "/* @modules */"
ENTRY_SCRIPTS = ("inject.js", "settings.js", "rooms.js", "toast.js", "game_mode.js",
                 "plaza-client.bundle.js")


def module_manifest():
    return json.loads((SCRIPTS_DIR / "manifest.json").read_text(encoding="utf-8"))


def read_script(name):
    source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    parts = module_manifest().get(name, [])
    if parts:
        if source.count(MODULE_MARKER) != 1:
            raise ValueError("script must have one module marker: " + name)
        modules = [(SCRIPTS_DIR / part).read_text(encoding="utf-8") for part in parts]
        source = source.replace(MODULE_MARKER, "\n".join(modules))
    if MODULE_MARKER in source:
        raise ValueError("script modules are not declared: " + name)
    return source


def script_files():
    """The resource inventory used by frozen release smoke checks."""
    parts = [part for modules in module_manifest().values() for part in modules]
    return tuple(dict.fromkeys((*ENTRY_SCRIPTS, "manifest.json", *parts, "plaza-config.json")))
