"""Хранение API-ключей: на Windows шифруются DPAPI (расшифровать может только
этот пользователь на этом компьютере), иначе хранятся как есть."""
from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(inp), None, None, None, None, 0x01, ctypes.byref(out))  # UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(value: str) -> str:
    if not value or sys.platform != "win32":
        return value
    try:
        return PREFIX + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")
    except Exception:
        return value


def unprotect(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _dpapi(base64.b64decode(value[len(PREFIX):]), False).decode("utf-8")
    except Exception:
        return ""   # ключ с другого компьютера/пользователя — нужно ввести заново
