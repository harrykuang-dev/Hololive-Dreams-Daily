"""Release the disposable click worker's managed inputs after it has exited.

No release occurs at import or construction. Callers must first terminate and
wait for their own input process, so its controller cannot post a later down.
"""
import ctypes
from ctypes import wintypes
import os

from .guarded_click import GuardedClickError


class _Keyboard(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class _Mouse(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _Hardware(ctypes.Structure):
    _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD)]


class _Value(ctypes.Union):
    _fields_ = [('ki', _Keyboard), ('mi', _Mouse), ('hi', _Hardware)]


class _Input(ctypes.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('type', wintypes.DWORD), ('value', _Value)]


class ManagedInputRelease:
    """Right Alt/left mouse exclusively reserved by the click worker's round.

    Windows cannot attribute GetAsyncKeyState to a physical or injected source.
    The caller must reserve this domain, rather than infer ownership from it.
    """
    def __init__(self, *, user=None, check_desktop=None):
        if user is None:
            if os.name != 'nt':
                raise GuardedClickError('Managed input cleanup requires Windows')
            user = ctypes.WinDLL('user32', use_last_error=True)
        self.user = user
        self.check_desktop = check_desktop or self._check_desktop
        user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
        user.SendInput.restype = wintypes.UINT
        user.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        user.MapVirtualKeyW.restype = wintypes.UINT
        user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user.GetAsyncKeyState.restype = ctypes.c_short

    def released(self):
        self.check_desktop()
        return not any(self.user.GetAsyncKeyState(key) & 0x8000 for key in (0xA5, 0x01))

    @staticmethod
    def _check_desktop():
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user.OpenInputDesktop.restype = wintypes.HANDLE
        user.GetThreadDesktop.argtypes = [wintypes.DWORD]
        user.GetThreadDesktop.restype = wintypes.HANDLE
        user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        user.GetUserObjectInformationW.restype = wintypes.BOOL
        user.CloseDesktop.argtypes = [wintypes.HANDLE]
        user.CloseDesktop.restype = wintypes.BOOL
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        desktop = user.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not desktop:
            raise GuardedClickError('Interactive input desktop is unavailable')
        def name(handle):
            value = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            if not user.GetUserObjectInformationW(handle, 2, value, ctypes.sizeof(value), ctypes.byref(needed)):
                raise GuardedClickError('Cannot verify interactive desktop identity')
            return value.value
        try:
            current = user.GetThreadDesktop(kernel.GetCurrentThreadId())
            if not current or not name(desktop) or name(desktop) != name(current):
                raise GuardedClickError('The worker is not on the active input desktop')
        finally:
            user.CloseDesktop(desktop)

    def release_after_exit(self, process, *, exclusive_input=False):
        if exclusive_input is not True:
            raise GuardedClickError('Managed input domain was not exclusively reserved')
        if process.poll() is None:
            raise GuardedClickError('Cannot release inputs while their worker can still post')
        self.check_desktop()
        values = (_Input*2)()
        values[0].type = 1  # INPUT_KEYBOARD
        values[0].ki.wVk = 0xA5  # VK_RMENU; match the pinned Maa Seize input.
        values[0].ki.wScan = self.user.MapVirtualKeyW(0xA5, 0)
        values[0].ki.dwFlags = 0x0002  # KEYEVENTF_KEYUP only.
        values[1].type = 0  # INPUT_MOUSE
        values[1].mi.dwFlags = 0x0004  # MOUSEEVENTF_LEFTUP only.
        if self.user.SendInput(2, values, ctypes.sizeof(_Input)) != 2:
            raise GuardedClickError('Managed input release was not fully accepted')
        if not self.released():
            raise GuardedClickError('Managed input release has not been confirmed')
