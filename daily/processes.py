"""Read-only process liveness and atomic local worker status files."""
import ctypes
import json
import os
from pathlib import Path
import time
import uuid


def parent_alive(pid):
    if os.name != 'nt':
        return os.getppid() == pid
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel.CloseHandle(handle)


def publish_status(path: Path, record):
    temporary = path.with_suffix(f'.{uuid.uuid4().hex}.pending')
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding='utf-8')
        deadline = time.monotonic()+.5
        while True:
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        temporary.unlink(missing_ok=True)
