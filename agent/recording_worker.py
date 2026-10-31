"""Read-only local Maa capture, isolated from the custom action IPC channel."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from daily.recording import FrameRecording, RecordingError


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


def run(config_path):
    config = json.loads(config_path.read_text(encoding='utf-8'))
    directory = config_path.parent
    recording = None

    def status(state, **details):
        path = directory / 'status.json'
        temporary = directory / f'{uuid.uuid4().hex}.pending'
        temporary.write_text(json.dumps({'job_id': config['job_id'], 'state': state,
                                        'updated_monotonic': time.monotonic(), **details}), encoding='utf-8')
        temporary.replace(path)

    try:
        # Null input methods make this controller incapable of issuing game input.
        capture = Win32Controller(config['hwnd'], MaaWin32ScreencapMethodEnum.FramePool,
                                  MaaWin32InputMethodEnum.Null, MaaWin32InputMethodEnum.Null)
        if not capture.post_connection().wait().succeeded:
            raise RecordingError('無法連接遊戲視窗錄影')
        recording = FrameRecording(capture, directory, 'game')
        recording.start()
        deadline = time.monotonic() + 7200
        while not (directory / 'stop').exists():
            recording.check()
            if not parent_alive(config['parent_pid']):
                raise RecordingError('主程序已結束，停止錄影')
            if time.monotonic() >= deadline:
                raise RecordingError('錄影已達兩小時上限')
            status('ready', paths=recording.paths(), frames=recording.frames)
            time.sleep(.1)
        recording.stop()
        status('complete', paths=recording.paths(), frames=recording.frames)
        return 0
    except Exception as error:
        if recording is not None:
            try:
                recording.stop()
            except Exception:
                pass
        status('failed', error=f'{type(error).__name__}: {error}')
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().config))
