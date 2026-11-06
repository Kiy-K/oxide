"""Windows file ChangeTime, which ``os.stat`` does not expose: its ``st_ctime`` there is the
creation time. Any write or timestamp edit moves ChangeTime, as ``st_ctime`` does elsewhere,
so a restored mtime cannot hide an edit from the capture cache."""

from __future__ import annotations

import functools
import sys
from typing import Any


@functools.cache
def _api() -> Any:
    if sys.platform != "win32":
        raise NotImplementedError("ChangeTime is read on Windows only")
    import ctypes
    from ctypes import wintypes

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declared, or 64-bit handles are truncated to C ints.
    dll.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    dll.CreateFileW.restype = wintypes.HANDLE
    dll.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    dll.GetFileInformationByHandleEx.restype = wintypes.BOOL
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    return dll


def change_time(source: str | int) -> int:
    """ChangeTime (100 ns ticks) of a file path or open file descriptor. Raises OSError."""
    if sys.platform != "win32":
        raise NotImplementedError("ChangeTime is read on Windows only")
    import ctypes
    import msvcrt

    class BasicInfo(ctypes.Structure):  # FILE_BASIC_INFO
        _fields_ = [
            ("created", ctypes.c_int64),
            ("accessed", ctypes.c_int64),
            ("written", ctypes.c_int64),
            ("changed", ctypes.c_int64),
            ("attributes", ctypes.c_uint32),
        ]

    api = _api()
    if isinstance(source, int):
        handle = msvcrt.get_osfhandle(source)
    else:
        # Attribute access only, never following a reparse point: no content access, so no
        # antivirus scan per file. READ_ATTRIBUTES, share all, OPEN_EXISTING.
        handle = api.CreateFileW(source, 0x80, 7, None, 3, 0x00200000, None)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    info = BasicInfo()
    try:
        # FileBasicInfo is information class 0.
        if not api.GetFileInformationByHandleEx(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if isinstance(source, str):
            api.CloseHandle(handle)
    return int(info.changed)
