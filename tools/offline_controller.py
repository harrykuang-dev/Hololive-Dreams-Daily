"""A screenshot-only Maa controller. Every input is rejected and counted."""
from maa.controller import CustomController


class OfflineController(CustomController):
    def __init__(self, frame):
        self.frame = frame
        self.input_attempts = []
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return 'offline-recording-no-input'
    def screencap(self): return self.frame.copy()
    def start_app(self, intent): return self.reject('start_app', intent)
    def stop_app(self, intent): return self.reject('stop_app', intent)
    def click(self, x, y): return self.reject('click', x, y)
    def swipe(self, *args): return self.reject('swipe', *args)
    def touch_down(self, *args): return self.reject('touch_down', *args)
    def touch_move(self, *args): return self.reject('touch_move', *args)
    def touch_up(self, *args): return self.reject('touch_up', *args)
    def click_key(self, *args): return self.reject('click_key', *args)
    def input_text(self, *args): return self.reject('input_text', *args)
    def key_down(self, *args): return self.reject('key_down', *args)
    def key_up(self, *args): return self.reject('key_up', *args)
    def relative_move(self, *args): return self.reject('relative_move', *args)

    def reject(self, action, *args):
        self.input_attempts.append((action, args))
        return False
