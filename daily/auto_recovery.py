"""Retain emergency handles while the independent watchdog owns the round."""
import os
import threading

from .game_process import GameProcess, GameProcessError, WindowsProcessBackend
from .input_release import ManagedInputRelease


class WatchdogRecovery:
    def __init__(self, watchdog, hwnd, *, game_pid, game_image, worker_pid, worker_creation,
                 game_factory=GameProcess, backend_factory=WindowsProcessBackend,
                 release_factory=ManagedInputRelease):
        if (type(worker_pid) is not int or worker_pid <= 0 or worker_pid in (game_pid, watchdog.pid, os.getpid())
                or type(worker_creation) is not int or worker_creation <= 0):
            raise GameProcessError('Invalid guarded worker identity')
        self.watchdog = watchdog
        self.backend = backend_factory()
        self.release_factory = release_factory
        self.armed = threading.Event()
        self.input_possible = threading.Event()
        self.error = None
        self.worker = self.backend.open_process(worker_pid)
        self.game = None
        try:
            if (self.backend.creation_time(self.worker) != worker_creation
                    or not self.backend.is_running(self.worker)):
                raise GameProcessError('Guarded worker identity changed')
            self.game = game_factory(hwnd, expected_pid=game_pid, expected_image=game_image)
            self.thread = threading.Thread(target=self._monitor, daemon=True)
            self.thread.start()
        except BaseException:
            self._close_handles()
            raise

    def _close_handles(self):
        try:
            if self.game is not None:
                self.game.close()
        finally:
            self.backend.close_handle(self.worker)

    def poll(self):
        """Input release checks only this retained handle, never a reused PID."""
        return None if self.backend.is_running(self.worker) else 0

    def _monitor(self):
        try:
            # Zero is emitted only after the watchdog accepted the final
            # Auto start acknowledgement and finished its own input cleanup.
            if self.watchdog.wait() == 0:
                return
            try:
                if self.armed.is_set():
                    self.game.emergency_stop()
            finally:
                if self.poll() is None:
                    self.backend.terminate(self.worker)
                    self.backend.wait_closed(self.worker)
            if self.input_possible.is_set():
                release = self.release_factory()
                if not release.released():
                    release.release_after_exit(self, exclusive_input=True)
        except Exception as error:
            self.error = error
        finally:
            self._close_handles()

    def finish(self, timeout=12):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise GameProcessError('Guarded worker recovery did not finish')
        if self.error is not None:
            raise GameProcessError('Guarded worker recovery failed') from self.error
