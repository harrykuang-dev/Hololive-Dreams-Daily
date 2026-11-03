import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from daily.auto_deadline import CountdownObservation
from daily.auto_supervisor import AutoGuardError, AutoSupervisor
from daily.contracts import AutoObservation, ResourceObservation, TaskConfig
from daily.guarded_click import GuardedClickError
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
        ack = 0
        def poll(self):
            if self.returncode is not None: return self.returncode
            if self.state == 'ready' and (self.directory/'arm.json').exists() and mode != 'never_arm':
                self.deadline = now[0]+25
                self.send('armed', deadline=self.deadline)
            if self.state == 'armed':
                click = self.read('click.json')
                if click and click['request']['sequence'] > self.ack:
                    if click['purpose'] != 'start' or mode != 'missing_start_ack':
                        self.ack = click['request']['sequence']
                        events.append(click['purpose'])
                message = self.read('observation.json')
                observed = message.get('observation', {})
                if observed.get('start_authorized') and message.get('input_sequence') == self.ack:
                    self.send('auto_verified', input_ack=self.ack)
                    self.returncode = 0
                else:
                    self.send('armed', deadline=self.deadline, input_ack=self.ack,
                              ready_seen=observed.get('scene') == 'multi_ready',
                              last_capture=observed.get('captured_at'))
            return self.returncode
        def read(self, name):
            path = self.directory/name
            return json.loads(path.read_text()) if path.exists() else {}
        def send(self, state, **details):
            self.state = state
            publish_status(self.directory/'status.json', {'state': state, 'job_id': self.job,
                'updated_monotonic': now[0], **details})
        def wait(self, **kwargs):
            assert 'stop_game' in events or (self.directory/'cancel_before_arm').exists()
            events.append('watchdog_cleanup')
            self.returncode = 2
            return self.returncode
    processes = []
    def launch(command, **kwargs):
        assert kwargs['creationflags'] & 0x01000000  # Independent of parent job teardown.
        if mode == 'breakaway_denied': raise OSError('Cannot break away from job')
        config = Path(command[-1])
        value = json.loads(config.read_text())
        assert value['game_pid'] == 99 and value['parent_creation'] == 12345
        process = Process()
        process.directory, process.job = config.parent, value['job_id']
        process.send('ready', game_pid=88 if mode == 'wrong_game' else 99, game_image=game.image,
                     input_protocol=1)
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
    assert events == ['watchdog_cleanup', 'release_game_handle']


def test_missing_arm_ack_closes_game_without_matching(tmp_path):
    manager, events, processes = harness(tmp_path, 'never_arm')
    manager.prepare()
    with pytest.raises(AutoGuardError, match='timed out'):
        manager.match(500, 400, captured_at=100)
    assert events == ['stop_game', 'watchdog_cleanup', 'release_game_handle']


def test_matching_click_requires_arm_and_cancel_closes_exact_game_first(tmp_path):
    manager, events, processes = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    assert processes[0].state == 'armed' and events == ['match']
    manager.close()
    assert events == ['match', 'stop_game', 'watchdog_cleanup', 'release_game_handle']


def test_dead_watchdog_during_preparation_triggers_game_shutdown(tmp_path):
    manager, events, processes = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    processes[0].returncode = 1
    with pytest.raises(AutoGuardError, match='healthy'):
        manager.observe(CountdownObservation('multi_song_select', 100))
    assert 'stop_game' in events


def test_preparation_observation_cannot_forge_final_start_authority(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    with pytest.raises(AutoGuardError, match='cannot authorize'):
        manager.observe(CountdownObservation('multi_ready', 100, True, 3, 30, True))
    assert 'stop_game' in events


def test_final_actual_auto_ack_does_not_close_game_and_guard_cannot_be_reused(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    permit = manager.prepare_start(TaskConfig('multi_live'),
        LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), 1))
    manager.verify_permit(permit)
    manager.perform_start(permit, 1150, 650)
    assert manager.phase == 'safe_auto'
    with pytest.raises(AutoGuardError, match='fresh prepared'):
        manager.match(500, 400, captured_at=100)
    manager.close()
    assert events == ['match', 'start', 'release_game_handle']


def test_blocked_trace_cannot_resume_matching_after_watchdog_exit(tmp_path):
    manager, events, processes = harness(tmp_path)
    manager.prepare()
    def trace(event, **details):
        if event == 'multi_matching_guarded':
            processes[0].returncode = 2
    manager.trace = trace
    with pytest.raises(AutoGuardError, match='healthy'):
        manager.match(500, 400, captured_at=100)
    assert 'stop_game' in events and 'match' not in events


@pytest.mark.parametrize('cost,budget', [(1, 0), (5, 1)])
def test_positive_auto_cannot_bypass_resource_gate(tmp_path, cost, budget):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    with pytest.raises(LiveBlocked):
        manager.prepare_start(TaskConfig('multi_live', multiplier=cost),
            LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), cost), budget)
    assert 'stop_game' in events


def test_bare_auto_observation_cannot_release_guard(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    with pytest.raises(AutoGuardError, match='permit'):
        manager.perform_start(AutoObservation(True, 3, 'multi_ready', 100), 1150, 650)
    assert 'stop_game' in events


def test_new_preparation_frame_invalidates_previously_minted_permit(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    permit = manager.prepare_start(TaskConfig('multi_live'),
        LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), 1))
    manager.observe(CountdownObservation('multi_ready', 100, True, 3, 30))
    with pytest.raises(AutoGuardError, match='permit'):
        manager.perform_start(permit, 1150, 650)


def test_missing_start_ack_does_not_release_independent_guard(tmp_path):
    manager, events, _ = harness(tmp_path, 'missing_start_ack')
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    permit = manager.prepare_start(TaskConfig('multi_live'),
        LiveState(AutoObservation(True, 3, 'multi_ready', 100), ResourceObservation(5, 10), 1))
    with pytest.raises(AutoGuardError, match='timed out'):
        manager.perform_start(permit, 1150, 650)
    assert 'stop_game' in events and manager.phase == 'closed'
    assert not json.loads((manager.directory/'observation.json').read_text())['observation']['start_authorized']


def test_preparation_cannot_click_the_start_control(tmp_path):
    manager, events, _ = harness(tmp_path)
    manager.prepare()
    manager.match(500, 400, captured_at=100)
    with pytest.raises(GuardedClickError, match='start region'):
        manager.click(1150, 650, captured_at=100)
    assert 'stop_game' in events and manager.phase == 'closed'
    assert 'prepare' not in events
