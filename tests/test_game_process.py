import pytest
import threading

from daily.game_process import GameProcess, GameProcessError


class FakeBackend:
    def __init__(self):
        self.pid = 999999
        self.image = r'D:\Games\hololive-Dreams.exe'
        self.running = True
        self.events = []

    def pid_for_window(self, hwnd): return self.pid
    def open_process(self, pid):
        self.events.append(('open', pid))
        return 'retained-process-object'
    def image_path(self, handle): return self.image
    def is_running(self, handle): return self.running
    def terminate(self, handle): self.events.append(('terminate', handle))
    def wait_closed(self, handle): self.events.append(('wait', handle))
    def close_handle(self, handle): self.events.append(('close', handle))


def test_binding_does_not_terminate_game_and_pid_reuse_cannot_redirect_shutdown():
    backend = FakeBackend()
    game = GameProcess(123, backend=backend)
    assert backend.events == [('open', 999999)]
    backend.pid = 888888  # A later lookup would target a different process.
    assert game.emergency_stop()
    assert backend.events[1:3] == [('terminate', 'retained-process-object'), ('wait', 'retained-process-object')]
    game.close()
    assert backend.events[-1] == ('close', 'retained-process-object')


@pytest.mark.parametrize('image', [r'D:\Other\python.exe', r'D:\Other\hololive-Dreams.exe.backup'])
def test_unrelated_executable_is_never_terminated(image):
    backend = FakeBackend()
    backend.image = image
    with pytest.raises(GameProcessError, match='not hololive'):
        GameProcess(123, backend=backend)
    assert not any(kind == 'terminate' for kind, _ in backend.events)
    assert backend.events[-1][0] == 'close'


def test_changed_pid_fails_before_acquiring_shutdown_handle():
    backend = FakeBackend()
    with pytest.raises(GameProcessError, match='identity'):
        GameProcess(123, expected_pid=888888, backend=backend)
    assert not backend.events


def test_changed_executable_path_releases_handle_without_shutdown():
    backend = FakeBackend()
    with pytest.raises(GameProcessError, match='identity'):
        GameProcess(123, expected_image=r'D:\Different\hololive-Dreams.exe', backend=backend)
    assert backend.events == [('open', 999999), ('close', 'retained-process-object')]


def test_already_exited_game_is_not_terminated_again():
    backend = FakeBackend()
    game = GameProcess(123, backend=backend)
    backend.running = False
    assert not game.emergency_stop()
    assert backend.events == [('open', 999999)]
    game.close()
    with pytest.raises(GameProcessError, match='closed'):
        game.emergency_stop()


def test_window_reuse_during_binding_is_rejected():
    backend = FakeBackend()
    pids = iter([999999, 888888])
    backend.pid_for_window = lambda hwnd: next(pids)
    with pytest.raises(GameProcessError, match='window identity'):
        GameProcess(123, backend=backend)
    assert backend.events == [('open', 999999), ('close', 'retained-process-object')]


def test_failed_executable_query_releases_handle_without_terminating():
    backend = FakeBackend()
    def fail(handle): raise GameProcessError('query failed')
    backend.image_path = fail
    with pytest.raises(GameProcessError, match='query'):
        GameProcess(123, backend=backend)
    assert backend.events == [('open', 999999), ('close', 'retained-process-object')]


def test_natural_exit_between_query_and_shutdown_is_not_a_failure():
    backend = FakeBackend()
    game = GameProcess(123, backend=backend)
    def exited(handle):
        backend.running = False
        raise GameProcessError('process exited meanwhile')
    backend.terminate = exited
    assert not game.emergency_stop()
    game.close()


def test_shutdown_denial_does_not_claim_the_game_was_closed():
    backend = FakeBackend()
    game = GameProcess(123, backend=backend)
    def denied(handle): raise GameProcessError('shutdown denied')
    backend.terminate = denied
    with pytest.raises(GameProcessError, match='denied'):
        game.emergency_stop()
    game.close()
    assert backend.events[-1] == ('close', 'retained-process-object')


def test_close_cannot_release_handle_while_emergency_shutdown_uses_it():
    backend = FakeBackend()
    entered, resume, closing, released = (threading.Event() for _ in range(4))
    errors = []
    def terminate(handle):
        entered.set()
        if not resume.wait(3):
            raise RuntimeError('test shutdown was not resumed')
        backend.events.append(('terminate', handle))
    def close_handle(handle):
        backend.events.append(('close', handle))
        released.set()
    backend.terminate, backend.close_handle = terminate, close_handle
    game = GameProcess(123, backend=backend)
    def run(action):
        try:
            action()
        except BaseException as error:
            errors.append(error)
    stopper = threading.Thread(target=lambda: run(game.emergency_stop))
    def close():
        closing.set()
        game.close()
    closer = threading.Thread(target=lambda: run(close))
    stopper.start()
    try:
        assert entered.wait(1)
        closer.start()
        assert closing.wait(1)
        assert not released.wait(.1)
    finally:
        resume.set()
        stopper.join(3)
        if closer.ident is not None:
            closer.join(3)
    assert not errors and not stopper.is_alive() and not closer.is_alive()
    assert backend.events[-3:] == [('terminate', 'retained-process-object'),
                                  ('wait', 'retained-process-object'),
                                  ('close', 'retained-process-object')]
