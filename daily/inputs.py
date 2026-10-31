"""Maa controller input ownership and guaranteed key release."""
from __future__ import annotations

from contextlib import contextmanager
import threading
import time


class Cancelled(RuntimeError):
    pass


class InputFailure(RuntimeError):
    pass


class Inputs:
    RIGHT_ALT = 0xA5

    def __init__(self, controller, *, stopping=lambda: False):
        self.controller = controller
        self.stopping = stopping
        self.held: set[int] = set()
        self._owner = threading.get_ident()

    def check(self):
        if threading.get_ident() != self._owner:
            raise InputFailure('Input ownership changed')
        if self.stopping():
            raise Cancelled('已停止')

    @staticmethod
    def wait(job):
        result = job.wait()
        if hasattr(result, 'succeeded') and not result.succeeded:
            raise InputFailure('Game input failed')

    def down(self, key: int):
        self.check()
        # Remember it before posting: a failed acknowledgement can still mean key-down happened.
        self.held.add(key)
        self.wait(self.controller.post_key_down(key))

    def up(self, key: int):
        self.wait(self.controller.post_key_up(key))
        self.held.discard(key)

    @contextmanager
    def cursor(self):
        try:
            self.down(self.RIGHT_ALT)
            self.pause(.08)
            self.wait(self.controller.post_relative_move(1, 0))
            self.pause(.08)
            yield
            self.pause(.05)
        finally:
            self.release_all()

    def click(self, x: int, y: int):
        self.check()
        with self.cursor():
            self.check()
            self.wait(self.controller.post_click(x, y))

    def key(self, key: int, duration: float = 0.05):
        if not 0 < duration <= 0.75:
            raise ValueError('Movement segments must be bounded to 750 ms')
        try:
            self.down(key)
            self.pause(duration)
        finally:
            self.release_all()

    def pause(self, duration: float):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.02, max(0, deadline - time.monotonic())))

    def release_all(self):
        failures = []
        for key in tuple(self.held):
            try:
                self.up(key)
            except Exception as error:
                failures.append(error)
        if failures:
            raise InputFailure('Failed to release all held keys') from failures[0]

