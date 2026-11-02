import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from daily.auto_deadline import CountdownObservation
from daily.auto_supervisor import AutoGuardError, AutoSupervisor
from daily.contracts import AutoObservation, ResourceObservation, TaskConfig
from daily.live_validation import LiveState, LiveBlocked
from daily.processes import publish_status


def harness(tmp_path, mode='normal'):
    now, events = [100.0], []
    backend = SimpleNamespace(pid_for_window=lambda hwnd: 99, is_running=lambda handle: True)
    game = SimpleNamespace(pid=99, image=r'D:\Games\hololive-Dreams.exe',
        handle='game-handle', backend=backend,
        emergency_stop=lambda: events.append('stop_game') or True,
        close=lambda: events.append('release_game_handle'))
    class Process:
        pid = 1234
        returncode = None
        def poll(self):
            if self.returncode is not None: return self.returncode
            path = self.directory/'observation.json'
            if path.exists() and json.loads(path.read_text())['observation'].get('start_authorized'):
                self.send('auto_verified')
                self.returncode = 0
            elif (self.directory/'arm.json').exists() and self.state == 'ready' and mode != 'never_arm':
                self.send('armed', deadline=now[0]+25)
            return self.returncode
        def send(self, state, **details):
            self.state = state
            publish_status(self.directory/'status.json', {'state': state, 'job_id': self.job,
                'updated_monotonic': now[0], **details})
        def terminate(self):
            events.append('stop_watchdog')
            self.returncode = -15
        def wait(self, **kwargs): return self.returncode
    processes = []
    def launch(command, **kwargs):
        assert kwargs['creationflags'] & 0x01000000  # Independent of parent job teardown.
        if mode == 'breakaway_denied': raise OSError('Cannot break away from job')
        config = Path(command[-1])
        value = json.loads(config.read_text())
        assert value['game_pid'] == 99 and value['parent_creation'] == 12345
        process = Process()
        process.directory, process.job = config.parent, value['job_id']
        process.send('ready', game_pid=88 if mode == 'wrong_game' else 99, game_image=game.image)
        processes.append(process)
        return process
    manager = AutoSupervisor(tmp_path, tmp_path/'python.exe', tmp_path/'sessions', 42,
        game_factory=lambda hwnd: game, launcher=launch, creation_time=lambda: 12345,
        clock=lambda: now[0], pause=lambda delay: now.__setitem__(0, now[0]+delay))
    return manager, events, processes


@pytest.mark.parametrize('mode', ['wrong_game', 'breakaway_denied'])
def test_failed_prepare_never_matches_or_closes_unarmed_game(tmp_path, mode):
    manager, events, processes = harness(tmp_path, mode)
    with pytest.raises((AutoGuardError, OSError)):
        manager.prepare()
    assert 'stop_game' not in events and manager.phase == 'closed'


def test_unarmed_readiness_can_be_closed_without_game_shutdown(tmp_path):
    manager, events, processes = harness(tmp_path)
    manager.prepare()
    assert manager.phase == 'prepared' and events == []
    manager.close()
    assert events == ['stop_watchdog', 'release_game_handle']


def test_missing_arm_ack_closes_game_before_removing_watchdog(tmp_path):
    manager, events, processes = harness(tmp_path, 'never_arm')
    manager.prepare()
    with pytest.raises(AutoGuardError, match='timed out'):
        manager.match(lambda: pytest.fail('Matching cannot precede arm acknowledgement'))
    assert events.index('stop_game') < events.index('stop_watchdog')


def test_matching_callback_requires_arm_and_cancel_closes_exact_game_first(tmp_path):
    manager, events, processes = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: events.append('match'))
    assert processes[0].state == 'armed' and events == ['match']
    manager.close()
    assert events == ['match', 'stop_game', 'stop_watchdog', 'release_game_handle']


def test_dead_watchdog_during_preparation_triggers_game_shutdown(tmp_path):
    manager, events, processes = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    processes[0].returncode = 1
    with pytest.raises(AutoGuardError, match='healthy'):
        manager.observe(CountdownObservation('multi_song_select', 100))
    assert 'stop_game' in events


def test_preparation_observation_cannot_forge_final_start_authority(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    with pytest.raises(AutoGuardError, match='cannot authorize'):
        manager.observe(CountdownObservation('multi_ready', 100, True, 3, 30, True))
    assert 'stop_game' in events


def test_final_actual_auto_ack_does_not_close_game_and_guard_cannot_be_reused(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    permit = manager.prepare_start(TaskConfig('multi_live'),
        LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), 1))
    manager.verify_permit(permit)
    manager.perform_start(permit, lambda preflight: preflight())
    assert manager.phase == 'safe_auto'
    with pytest.raises(AutoGuardError, match='fresh prepared'):
        manager.match(lambda: pytest.fail('No second round may reuse the latch'))
    manager.close()
    assert events == ['release_game_handle']


def test_blocked_trace_cannot_resume_matching_after_watchdog_exit(tmp_path):
    manager, events, processes = harness(tmp_path)
    manager.prepare()
    def trace(event, **details):
        if event == 'multi_matching_guarded':
            processes[0].returncode = 2
    manager.trace = trace
    with pytest.raises(AutoGuardError, match='healthy'):
        manager.match(lambda: pytest.fail('No matching after blocking audit and watchdog exit'))
    assert 'stop_game' in events


@pytest.mark.parametrize('cost,budget', [(1, 0), (5, 1)])
def test_positive_auto_cannot_bypass_resource_gate(tmp_path, cost, budget):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    with pytest.raises(LiveBlocked):
        manager.prepare_start(TaskConfig('multi_live', multiplier=cost),
            LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), cost), budget)
    assert 'stop_game' in events


def test_bare_auto_observation_cannot_release_guard(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    with pytest.raises(AutoGuardError, match='permit'):
        manager.perform_start(AutoObservation(True, 3, 'multi_ready', 100), lambda preflight: None)
    assert 'stop_game' in events


def test_new_preparation_frame_invalidates_previously_minted_permit(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    permit = manager.prepare_start(TaskConfig('multi_live'),
        LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), 1))
    manager.observe(CountdownObservation('multi_ready', 100, True, 3, 30))
    with pytest.raises(AutoGuardError, match='permit'):
        manager.perform_start(permit, lambda preflight: None)


def test_failed_start_input_does_not_release_independent_guard(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    permit = manager.prepare_start(TaskConfig('multi_live'),
        LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), 1))
    def failed(preflight):
        preflight()
        raise RuntimeError('controller input failed')
    with pytest.raises(RuntimeError, match='controller'):
        manager.perform_start(permit, failed)
    assert 'stop_game' in events and manager.phase == 'closed'


@pytest.mark.parametrize('checks', [0, 2])
def test_start_callback_must_perform_exactly_one_final_input_check(tmp_path, checks):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(lambda: None)
    permit = manager.prepare_start(TaskConfig('multi_live'),
        LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), 1))
    def action(preflight):
        for _ in range(checks):
            preflight()
    with pytest.raises(AutoGuardError, match='preflight'):
        manager.perform_start(permit, action)
    assert 'stop_game' in events and manager.phase == 'closed'
