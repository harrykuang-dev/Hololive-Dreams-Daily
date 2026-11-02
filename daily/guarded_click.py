"""Fresh, bounded click requests for a disposable multiplayer input process.

This protocol is not connected to matchmaking. It deliberately has no rhythm,
movement-key or arbitrary executable command.
"""
from dataclasses import dataclass
import math


class GuardedClickError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClickRequest:
    job_id: str
    sequence: int
    x: int
    y: int
    captured_at: float

    @classmethod
    def parse(cls, message, job_id, *, last_sequence, now):
        keys = {'job_id', 'sequence', 'x', 'y', 'captured_at'}
        if not isinstance(message, dict) or set(message) != keys:
            raise GuardedClickError('Invalid click message fields')
        value = cls(**message)
        if (not isinstance(value.job_id, str) or value.job_id != job_id
                or type(value.sequence) is not int or value.sequence != last_sequence+1
                or type(value.x) is not int or not 0 <= value.x < 1280
                or type(value.y) is not int or not 0 <= value.y < 720):
            raise GuardedClickError('Click identity, sequence or coordinate is invalid')
        value.check_fresh(now)
        return value

    def check_fresh(self, now):
        if (type(now) not in (float, int) or not math.isfinite(now)
                or type(self.captured_at) not in (float, int) or not math.isfinite(self.captured_at)
                or not 0 <= now-self.captured_at <= 2):
            raise GuardedClickError('Click evidence expired')


def execute_click(request, inputs, *, clock, check_target):
    """Run only on a dedicated local controller, never the GUI's RPC queue."""
    def check():
        request.check_fresh(clock())
        check_target()
    # Inputs rechecks this guard after audit hooks and immediately before each
    # new post. Cleanup bypasses the guard, including expired requests.
    previous = inputs.guard
    def guard():
        previous()
        check()
    inputs.guard = guard
    try:
        check()
        inputs.click(request.x, request.y, before_post=check)
    finally:
        inputs.guard = previous
        inputs.release_all()


class FrameCoordinates:
    """Map normalized navigation coordinates to the local SDK capture size."""
    def __init__(self, controller):
        self.controller = controller
        self.width = self.height = 0

    def update_frame(self, shape):
        if len(shape) < 2:
            raise GuardedClickError('Missing local controller capture dimensions')
        height, width = shape[:2]
        if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
                or abs(width/height-16/9) > .04):
            raise GuardedClickError('Local controller requires 16:9 game content')
        self.width, self.height = width, height

    def post_click(self, x, y):
        if not self.width or not self.height:
            raise GuardedClickError('No verified local capture before click')
        return self.controller.post_click(min(self.width-1, round(x*self.width/1280)),
                                          min(self.height-1, round(y*self.height/720)))

    def post_key_down(self, key): return self.controller.post_key_down(key)
    def post_key_up(self, key): return self.controller.post_key_up(key)
    def post_relative_move(self, x, y): return self.controller.post_relative_move(x, y)
