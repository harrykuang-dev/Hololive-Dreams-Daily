"""Check whether a foreground capture still targets the game window."""
import ctypes
from ctypes import wintypes


def game_in_foreground(hwnd):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    foreground = user32.GetForegroundWindow
    foreground.restype = wintypes.HWND
    iconic = user32.IsIconic
    iconic.argtypes = [wintypes.HWND]
    visible = user32.IsWindowVisible
    visible.argtypes = [wintypes.HWND]
    return foreground() == hwnd and not iconic(hwnd) and bool(visible(hwnd))


def request_game_foreground(hwnd):
    """Try once after Start; Windows can deny activation from a worker."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    iconic = user32.IsIconic
    iconic.argtypes = [wintypes.HWND]
    if iconic(hwnd):
        restore = user32.ShowWindowAsync
        restore.argtypes = [wintypes.HWND, ctypes.c_int]
        restore(hwnd, 9)  # SW_RESTORE
    activate = user32.SetForegroundWindow
    activate.argtypes = [wintypes.HWND]
    activate(hwnd)
