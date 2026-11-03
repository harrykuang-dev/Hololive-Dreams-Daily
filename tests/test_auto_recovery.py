from threading import Event
from types import SimpleNamespace

import pytest

from daily.auto_recovery import WatchdogRecovery
from daily.game_process import GameProcessError


@pytest.mark.parametrize('mode', ['crash', 'verified', 'unarmed', 'game_stop_failure'])
def test_watchdog_exit_recovers_only_retained_handles(mode):
    events = []
    exited = Event()
    worker_alive = True

    def wait_watchdog():
        assert exited.wait(2)
        return 0 if mode == 'verified' else 1

    def stop_game():
        events.append('stop_game')
        if mode == 'game_stop_failure':
            raise GameProcessError('Game did not exit')

    def terminate(handle):
        assert handle == 'retained_input'
        events.append('terminate_input')

    def wait_input(handle):
        nonlocal worker_alive
        assert handle == 'retained_input'
        worker_alive = False
        events.append('wait_input')

    def release(process, *, exclusive_input):
        assert exclusive_input and process.poll() == 0
        events.append('release_inputs')

    backend = SimpleNamespace(open_process=lambda pid: 'retained_input', creation_time=lambda handle: 123,
        is_running=lambda handle: worker_alive, terminate=terminate, wait_closed=wait_input,
        close_handle=lambda handle: events.append('close_input'))
    game = SimpleNamespace(emergency_stop=stop_game, close=lambda: events.append('close_game'))
    recovery = WatchdogRecovery(SimpleNamespace(pid=4, wait=wait_watchdog), 5, game_pid=6, game_image='game',
        worker_pid=7, worker_creation=123, game_factory=lambda *a, **k: game, backend_factory=lambda: backend,
        release_factory=lambda: SimpleNamespace(released=lambda: False, release_after_exit=release))
    if mode != 'unarmed':
        recovery.armed.set()
        recovery.input_possible.set()
    exited.set()

    if mode == 'game_stop_failure':
        with pytest.raises(GameProcessError, match='recovery failed'):
            recovery.finish()
    else:
        recovery.finish()
    assert events == {
        'crash': ['stop_game', 'terminate_input', 'wait_input', 'release_inputs', 'close_game', 'close_input'],
        'game_stop_failure': ['stop_game', 'terminate_input', 'wait_input', 'close_game', 'close_input'],
        'unarmed': ['terminate_input', 'wait_input', 'close_game', 'close_input'],
        'verified': ['close_game', 'close_input'],
    }[mode]


def test_recovery_rejects_reused_worker_pid_before_acquiring_game():
    handles = []
    backend = SimpleNamespace(open_process=lambda pid: 'wrong_creation', creation_time=lambda handle: 321,
                              close_handle=handles.append)
    with pytest.raises(GameProcessError, match='identity changed'):
        WatchdogRecovery(SimpleNamespace(pid=4), 5, game_pid=6, game_image='game',
            worker_pid=7, worker_creation=123, backend_factory=lambda: backend,
            game_factory=lambda *a, **k: pytest.fail('Worker must be verified first'))
    assert handles == ['wrong_creation']
