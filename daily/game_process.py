"""Pin one verified game process for multiplayer Auto emergency shutdown.

No process is terminated at import, construction or handle acquisition.
This is not used by the currently disabled multiplayer runner.
"""
import ctypes
from ctypes import wintypes
from pathlib import PureWindowsPath
import os


class GameProcessError(RuntimeError):
    pass


class WindowsProcessBackend:
    def __init__(self):
        if os.name != 'nt':
            raise GameProcessError('Game process supervision requires Windows')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                         wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateProcess.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        self.kernel.GetProcessTimes.restype = wintypes.BOOL

    def pid_for_window(self, hwnd):
        pid = wintypes.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise GameProcessError('Game window no longer exists')
        return pid.value

    def open_process(self, pid):
        # SYNCHRONIZE | QUERY_LIMITED_INFORMATION | TERMINATE. Lack of shutdown
        # permission must fail before matchmaking, not after its timer starts.
        handle = self.kernel.OpenProcess(0x100000 | 0x1000 | 0x1, False, pid)
        if not handle:
            raise GameProcessError('Cannot acquire the game supervision handle')
        return handle

    def image_path(self, handle):
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise GameProcessError('Cannot verify the game executable')
        return buffer.value

    def open_signal_process(self, pid):
        handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            raise GameProcessError('Cannot acquire the parent supervision handle')
        return handle

    def creation_time(self, handle):
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel), ctypes.byref(user)):
            raise GameProcessError('Cannot verify parent process creation time')
        return (created.dwHighDateTime << 32) | created.dwLowDateTime

    def is_running(self, handle):
        result = self.kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 0x102):
            raise GameProcessError('Cannot query the supervised process')
        return result == 0x102

    def terminate(self, handle):
        if not self.kernel.TerminateProcess(handle, 1):
            raise GameProcessError('Emergency game shutdown failed')

    def wait_closed(self, handle):
        if self.kernel.WaitForSingleObject(handle, 5000) != 0:
            raise GameProcessError('Game shutdown did not finish')

    def close_handle(self, handle):
        self.kernel.CloseHandle(handle)


class GameProcess:
    def __init__(self, hwnd, *, expected_pid=None, expected_image=None, backend=None):
        if type(hwnd) is not int or hwnd <= 0:
            raise GameProcessError('Invalid game window')
        self.backend = backend if backend is not None else WindowsProcessBackend()
        self.handle = None
        self.pid = self.backend.pid_for_window(hwnd)
        if self.pid == os.getpid() or (expected_pid is not None and self.pid != expected_pid):
            raise GameProcessError('Game process identity changed')
        try:
            self.handle = self.backend.open_process(self.pid)
            self.image = self.backend.image_path(self.handle)
            if PureWindowsPath(self.image).name.casefold() != 'hololive-dreams.exe':
                raise GameProcessError('The supervised window is not hololive-Dreams')
            if (expected_image is not None and
                    PureWindowsPath(self.image) != PureWindowsPath(expected_image)):
                raise GameProcessError('Game executable identity changed')
            if not self.backend.is_running(self.handle):
                raise GameProcessError('The game has already exited')
            if self.backend.pid_for_window(hwnd) != self.pid:
                raise GameProcessError('Game window identity changed during binding')
        except Exception:
            self.close()
            raise

    def emergency_stop(self):
        if self.handle is None:
            raise GameProcessError('Game supervision handle is closed')
        if not self.backend.is_running(self.handle):
            return False
        # Use the retained process handle, never look up a name or reuse a PID.
        try:
            self.backend.terminate(self.handle)
        except GameProcessError:
            # The game can exit naturally between the liveness query and
            # TerminateProcess. Confirm that outcome using the SAME handle.
            if not self.backend.is_running(self.handle):
                return False
            raise
        self.backend.wait_closed(self.handle)
        return True

    def close(self):
        if self.handle is not None:
            self.backend.close_handle(self.handle)
            self.handle = None


class ParentProcess:
    """Retain a parent handle and verify its creation time before arming."""
    def __init__(self, pid, *, expected_creation, backend=None):
        if type(pid) is not int or pid <= 0 or type(expected_creation) is not int or expected_creation <= 0:
            raise GameProcessError('Invalid parent identity')
        self.backend = backend if backend is not None else WindowsProcessBackend()
        self.handle = self.backend.open_signal_process(pid)
        try:
            if self.backend.creation_time(self.handle) != expected_creation:
                raise GameProcessError('Parent process identity changed')
            if not self.backend.is_running(self.handle):
                raise GameProcessError('Parent process already exited')
        except Exception:
            self.close()
            raise

    def alive(self, _pid=None):
        return self.handle is not None and self.backend.is_running(self.handle)

    def close(self):
        if self.handle is not None:
            self.backend.close_handle(self.handle)
            self.handle = None


def current_creation_time():
    backend = WindowsProcessBackend()
    handle = backend.open_signal_process(os.getpid())
    try:
        return backend.creation_time(handle)
    finally:
        backend.close_handle(handle)
