"""Current-user DPAPI storage for the plaza anonymous credential."""

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

FILE_NAME = "plaza_auth.json"
TEMP_NAME = "plaza_auth.tmp"


def crypt(data, decrypt=False):
    if os.name != "nt":
        raise OSError("DPAPI requires Windows")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("DPAPI failed")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class CredentialStore:
    def __init__(self, directory, base):
        self.path = Path(directory) / FILE_NAME
        self.base = base

    def load(self):
        if self.path.is_symlink():
            raise OSError("Linked credential file")
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 8192:
            raise ValueError("Invalid credential file")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        if saved.get("base") != self.base:
            return None
        return crypt(base64.b64decode(saved["protected"], validate=True), decrypt=True).decode("ascii")

    def save(self, token):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(TEMP_NAME)
        if self.path.is_symlink() or temporary.is_symlink():
            raise OSError("Linked credential file")
        payload = {"base": self.base, "protected": base64.b64encode(crypt(token.encode("ascii"))).decode("ascii")}
        try:
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(payload, output)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self):
        if self.path.is_symlink():
            raise OSError("Linked credential file")
        self.path.unlink(missing_ok=True)
