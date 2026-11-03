import json
from types import SimpleNamespace

import pytest

from daily.auto_watch import watch
from daily.watch_mailbox import read_message


def scenario(tmp_path, mode, *, failed_diagnostics=False):
    now = [100.0]
    events = []
    states = []
    alive = [True]
    job = 'one-round-job'
    config = {'job_id': job, 'parent_pid': 1001, 'game_pid': 2002,
              'hwnd': 3003, 'game_image': r'D:\Games\hololive-Dreams.exe'}
    def write(name, value):
        (tmp_path/name).write_text(json.dumps(value), encoding='utf-8')
    def stop():
        events.append('stop_exact_game')
        return True
    game = SimpleNamespace(pid=2002, image=config['game_image'],
        emergency_stop=stop, close=lambda: events.append('release_handle'))
    def factory(hwnd, **identity):
        assert hwnd == 3003
        assert identity == {'expected_pid': 2002, 'expected_image': config['game_image']}
        events.append('bind_own_game_handle')
        return game
    def publish(state, **details):
        states.append((state, details))
        if failed_diagnostics and state in ('closing_game', 'game_closed', 'failed'):
            raise OSError('disk full')
        if state == 'ready':
            write('arm.json', {'job_id': job, 'decision': 'arm', 'captured_at': now[0]})
        if state != 'armed': return
        observation = {'scene': 'multi_ready', 'captured_at': now[0],
                       'auto_enabled': True, 'auto_remaining': 3, 'start_authorized': True}
        if mode in ('auto', 'auto_parent_exit', 'auto_abort', 'slow_auto_read'):
            write('observation.json', {'job_id': job, 'observation': observation})
            if mode == 'auto_parent_exit': alive[0] = False
            if mode == 'auto_abort': (tmp_path/'abort').touch()
        elif mode == 'wrong_job':
            write('observation.json', {'job_id': 'old-round', 'observation': observation})
        elif mode == 'stale':
            observation['captured_at'] -= 3
            write('observation.json', {'job_id': job, 'observation': observation})
        elif mode == 'parent_exit':
            alive[0] = False
        elif mode == 'abort':
            (tmp_path/'abort').touch()
        elif mode in ('home', 'multi_ready'):
            write('disarm.json', {'job_id': job, 'captured_at': now[0], 'scene': mode})
        elif mode == 'bad_observation':
            write('observation.json', {'job_id': job, 'observation': {'scene': ['bad'], 'captured_at': now[0]}})
    def read(path, job_id):
        value = read_message(path, job_id)
        if mode == 'slow_auto_read' and path.name == 'observation.json': now[0] += 30
        return value
    channel = SimpleNamespace(publish=publish, error=None,
        message=lambda name: read(tmp_path/(name+'.json'), job),
        flag=lambda name: (tmp_path/name).exists())
    code = watch(config, channel, game_factory=factory,
                 alive=lambda pid: alive[0], clock=lambda: now[0],
                 pause=lambda _: now.__setitem__(0, now[0]+.5))
    return code, states, events


@pytest.mark.parametrize('mode', ['no_observations', 'wrong_job', 'stale', 'multi_ready', 'home', 'bad_observation'])
def test_missing_or_invalid_evidence_closes_only_owned_game_at_deadline(tmp_path, mode):
    code, states, events = scenario(tmp_path, mode)
    assert code == 2
    assert events == ['bind_own_game_handle', 'stop_exact_game', 'release_handle']
    assert states[-1][0] == 'game_closed'
    assert states[-1][1]['reason'] == 'auto_verification_deadline'


@pytest.mark.parametrize('mode', ['parent_exit', 'abort', 'auto_parent_exit', 'auto_abort', 'slow_auto_read'])
def test_parent_loss_and_cancel_close_game_before_unverified_auto_start(tmp_path, mode):
    code, states, events = scenario(tmp_path, mode)
    assert code == 2 and events[-2:] == ['stop_exact_game', 'release_handle']


def test_final_authorization_disarms_without_closing_game(tmp_path):
    code, states, events = scenario(tmp_path, 'auto')
    assert code == 0
    assert events == ['bind_own_game_handle', 'release_handle']
    assert states[-1][0] == 'auto_verified'


def test_failed_diagnostics_cannot_block_emergency_shutdown(tmp_path):
    code, states, events = scenario(tmp_path, 'abort', failed_diagnostics=True)
    assert code == 2 and 'stop_exact_game' in events


def test_parent_loss_before_arm_does_not_close_game(tmp_path):
    events = []
    game = SimpleNamespace(pid=2, image='game', close=lambda: events.append('close_handle'),
                           emergency_stop=lambda: pytest.fail('Not armed; no matchmaking'))
    channel = SimpleNamespace(publish=lambda *args, **kwargs: None, error=None,
                              message=lambda name: None, flag=lambda name: False)
    code = watch({'job_id': 'job', 'parent_pid': 1, 'game_pid': 2, 'game_image': 'game', 'hwnd': 3}, channel,
                 game_factory=lambda *args, **kwargs: game, alive=lambda pid: False)
    assert code == 2 and events == ['close_handle']


@pytest.mark.parametrize('mode', ['released', 'held', 'wrong_sequence', 'parent_loss', 'prepare_targets_start'])
def test_guarded_lane_release_is_bound_to_this_start_and_cleanup_order(mode):
    now = [100.0]
    events = []
    messages = {}
    job = 'guarded-round'
    config = dict(job_id=job, parent_pid=1, game_pid=2, game_image='game', hwnd=3)
    game = SimpleNamespace(pid=2, image='game', handle=object(),
        backend=SimpleNamespace(is_running=lambda _: True),
        emergency_stop=lambda: events.append('stop_game') or True,
        close=lambda: events.append('close_game_handle'))

    def click(purpose, sequence, x, y):
        messages['click'] = dict(job_id=job, purpose=purpose, request=dict(
            job_id=job, sequence=sequence, x=x, y=y, captured_at=now[0]))

    def evidence(*, authorized=False, sequence=None, captured_at=None):
        messages['observation'] = dict(job_id=job, input_sequence=sequence, observation=dict(
            scene='multi_ready', captured_at=now[0] if captured_at is None else captured_at,
            auto_enabled=True, auto_remaining=3, start_authorized=authorized))

    class Lane:
        ack = None
        error = None
        cleanup_complete = False
        process = SimpleNamespace(poll=lambda: None)

        def prepare(self):
            events.append('prepare_input')

        def dispatch(self, request):
            sequence = request['sequence']
            events.append(f'click_{sequence}')
            self.ack = dict(sequence=sequence if sequence == 1 or mode != 'wrong_sequence' else 1,
                            captured_at=request['captured_at'], released=sequence == 1 or mode != 'held')
            if sequence == 2:
                evidence(authorized=True, sequence=2, captured_at=request['captured_at'])
            return sequence

        def abort(self):
            events.append('cleanup_input')
            self.cleanup_complete = True

    def publish(state, **details):
        if state == 'ready':
            assert details['input_protocol'] == 1
            messages['arm'] = dict(job_id=job, decision='arm', captured_at=now[0])
        elif state == 'armed' and 'click_1' not in events:
            click('match', 1, 500, 400)
        elif state == 'armed' and details.get('input_ack') == 1 and messages['click']['request']['sequence'] == 1:
            evidence()
            click('prepare' if mode == 'prepare_targets_start' else 'start', 2, 1150, 650)
        elif state == 'auto_verified':
            events.append('release_guard')

    lane = Lane()
    channel = SimpleNamespace(publish=publish, error=None, message=messages.get, flag=lambda _: False)
    code = watch(config, channel, game_factory=lambda *a, **k: game, lane_factory=lambda _: lane,
                 alive=lambda _: mode != 'parent_loss' or 'click_2' not in events,
                 clock=lambda: now[0], pause=lambda delay: now.__setitem__(0, now[0]+delay))
    if mode == 'released':
        assert code == 0 and events == ['prepare_input', 'click_1', 'click_2', 'cleanup_input',
                                        'release_guard', 'close_game_handle']
    else:
        assert code != 0 and events[-3:] == ['stop_game', 'cleanup_input', 'close_game_handle']
        assert 'release_guard' not in events
        assert ('click_2' not in events) == (mode == 'prepare_targets_start')
