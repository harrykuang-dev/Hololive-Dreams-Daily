import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from daily import actions
from daily.contracts import TaskResult, TaskStatus
from daily.recording import RecordingError


@pytest.fixture
def recorded_action(monkeypatch, tmp_path):
    events = []

    class Recording:
        hwnd = 123

        def __init__(self, *args):
            pass

        def start(self):
            events.append('start')

        def check(self):
            pass

        def mark(self, event, **details):
            events.append(event)

        def paths(self):
            return {'video': 'test.avi', 'timeline': 'test.jsonl'}

        def stop(self):
            events.append('stop')

    class Navigator:
        def __init__(self, *args, **kwargs):
            events.append('navigator')
            self.inputs = SimpleNamespace(release_all=lambda: events.append('release'))

    monkeypatch.setenv('PI_USER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(actions, 'ROOT', tmp_path)
    monkeypatch.setattr(actions, 'GameRecording', Recording)
    monkeypatch.setattr(actions, 'game_in_foreground', lambda hwnd: hwnd == Recording.hwnd)
    monkeypatch.setattr(actions, 'Navigator', Navigator)
    monkeypatch.setattr(actions, 'read_settings', lambda *args: {'task': 'connect'})
    monkeypatch.setattr(actions, 'recording_directory', lambda *args: tmp_path)
    action = actions.RunDailyTask()
    return SimpleNamespace(action=action, events=events, recording=Recording,
                           context=SimpleNamespace(tasker=SimpleNamespace(controller=object(), stopping=False)),
                           args=SimpleNamespace(node_name='Daily_connect'),
                           result=lambda: json.loads(next((tmp_path / 'daily/results').glob('*.json')).read_text(encoding='utf-8')))


def test_startup_failure_never_enters_navigation(recorded_action, monkeypatch):
    test = recorded_action

    def fails(self):
        raise RecordingError('no recording')

    monkeypatch.setattr(test.recording, 'start', fails)
    assert not test.action.run(test.context, test.args)
    assert 'navigator' not in test.events
    assert test.result()['result']['status'] == 'failed'


def test_competing_foreground_prevents_recording_and_navigation(recorded_action, monkeypatch):
    test = recorded_action
    activations = []
    monkeypatch.setattr(actions, 'game_in_foreground', lambda _: False)
    monkeypatch.setattr(actions, 'request_game_foreground', activations.append)
    ticks = iter((0, 1, 15))
    monkeypatch.setattr(actions.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(actions.time, 'sleep', lambda _: None)
    assert not test.action.run(test.context, test.args)
    assert activations == [123]
    assert test.events == []
    assert test.result()['result']['reason'] == '等待遊戲前景逾時，未開始任何日常操作'


def test_delayed_foreground_allows_recording_and_navigation(recorded_action, monkeypatch):
    test = recorded_action
    checks = iter((False, False, True))
    monkeypatch.setattr(actions, 'game_in_foreground', lambda _: next(checks))
    monkeypatch.setattr(actions, 'request_game_foreground', lambda _: test.events.append('request_focus'))
    monkeypatch.setattr(actions.time, 'sleep', lambda _: test.events.append('wait'))
    monkeypatch.setattr(test.action, 'execute', lambda *args, **kwargs:
                        TaskResult(TaskStatus.COMPLETE, 'done'))
    assert test.action.run(test.context, test.args)
    assert test.events[:4] == ['request_focus', 'wait', 'start', 'navigator']


@pytest.mark.parametrize('cancel', [False, True])
def test_early_routine_waits_before_foreground_recording_or_input(recorded_action, monkeypatch, cancel):
    test = recorded_action
    from daily.calendar import DailyAttempts
    moments = iter(datetime.fromisoformat(s) for s in
                   ('2026-11-24T04:00:19+08:00', '2026-11-24T04:01:00+08:00'))
    ledger = DailyAttempts(None, not_before='2026-11-02T04:05:00+08:00', clock=lambda: next(moments))
    monkeypatch.setattr(actions.DailyAttempts, 'from_runtime', lambda *args: ledger)
    monkeypatch.setattr(actions, 'read_settings', lambda *args: {'task': 'daily_routine'})
    monkeypatch.setattr(actions, 'wait_for_game_foreground', lambda *args: test.events.append('foreground'))
    monkeypatch.setattr(test.action, 'execute', lambda *args, **kwargs:
                        TaskResult(TaskStatus.COMPLETE, 'done'))

    def wait(seconds):
        test.events.append('wait')
        test.context.tasker.stopping = cancel

    monkeypatch.setattr(actions.time, 'sleep', wait)
    assert test.action.run(test.context, test.args) is not cancel
    if cancel:
        assert test.events == ['wait']
        assert test.result()['result']['status'] == 'cancelled'
    else:
        assert test.events[:4] == ['wait', 'foreground', 'start', 'navigator']


@pytest.mark.parametrize('task', ['cooking', 'jump_rope'])
def test_skipped_minigame_does_not_pass_gui_task(recorded_action, monkeypatch, task):
    test = recorded_action
    monkeypatch.setattr(actions, 'read_settings', lambda *args: {'task': task, 'rounds': '1'})
    monkeypatch.setattr(test.action, 'execute', lambda *args, **kwargs:
                        TaskResult(TaskStatus.SKIPPED, 'budget prevents start'))
    assert not test.action.run(test.context, test.args)
    assert test.result()['result']['status'] == 'skipped'


def test_release_and_recording_finish_follow_task_result(recorded_action, monkeypatch):
    test = recorded_action
    monkeypatch.setattr(test.action, 'execute', lambda *args, **kwargs:
                        TaskResult(TaskStatus.COMPLETE, 'done', evidence='test.png'))
    assert test.action.run(test.context, test.args)
    assert test.events.index('start') < test.events.index('navigator')
    assert test.events.index('release') < test.events.index('task_end') < test.events.index('stop')
    assert test.result()['recording']['video'] == 'test.avi'


def test_video_disabled_retains_local_audit_and_daily_reservations(recorded_action, monkeypatch, tmp_path):
    test = recorded_action
    (tmp_path / 'config').mkdir()
    shared = tmp_path / 'attempts'
    (tmp_path / 'config/recording.json').write_text(
        json.dumps({'video': False, 'attempt_directory': str(shared)}), encoding='utf-8')
    test.context.tasker.controller = SimpleNamespace(info={'type': 'win32', 'hwnd': 123})

    def execute(navigator, config, *, progress):
        ledger = navigator.daily_attempts
        attempt = ledger.reserve('lottery')
        attempt.finish(evidence='confirmed.png')
        assert set(ledger.completed_tasks()) == {'lottery'}
        return TaskResult(TaskStatus.COMPLETE, 'done')

    monkeypatch.setattr(test.action, 'execute', execute)
    assert test.action.run(test.context, test.args)
    assert test.events == ['navigator', 'release']
    audit = test.result()['recording']
    assert set(audit) == {'timeline'}
    timeline = next(tmp_path.glob('daily/audit/*/events.jsonl'))
    events = [json.loads(line)['event'] for line in timeline.read_text(encoding='utf-8').splitlines()]
    assert events == ['audit_started', 'task_result', 'task_result', 'task_end', 'audit_stopped']
    assert not list(tmp_path.rglob('*.avi'))


def test_runtime_error_preserves_known_round_and_pending_round(recorded_action, monkeypatch):
    test = recorded_action

    def execute(*args, progress):
        progress(TaskResult(TaskStatus.RUNNING, 'second round', 1, 'first.png', pending_round=True))
        raise RecordingError('capture lost')

    monkeypatch.setattr(test.action, 'execute', execute)
    assert not test.action.run(test.context, test.args)
    result = test.result()['result']
    assert result['status'] == 'failed'
    assert result['completed_rounds'] == 1 and result['pending_round']
    assert 'release' in test.events and 'stop' in test.events


def test_account_displacement_stops_remaining_routine_steps(recorded_action, monkeypatch):
    test = recorded_action
    monkeypatch.setattr(actions, 'read_settings', lambda *args: {'task': 'daily_routine'})
    attempted = []

    def displaced(navigator, options, *, progress):
        if options.task == 'daily_routine':
            navigator.inputs.check = lambda: None
            return test.action.run_routine(navigator, options, progress)
        attempted.append(options.task)
        raise actions.SessionDisplaced('other-device.png')

    monkeypatch.setattr(test.action, 'execute', displaced)
    assert not test.action.run(test.context, test.args)
    assert attempted == ['holowork']
    result = test.result()['result']
    assert result['status'] == 'failed' and result['evidence'] == 'other-device.png'
    assert '其他裝置登入' in result['reason']
    assert 'release' in test.events and 'stop' in test.events


def test_unverified_recording_cannot_report_complete(recorded_action, monkeypatch):
    test = recorded_action
    monkeypatch.setattr(test.action, 'execute', lambda *args, **kwargs:
                        TaskResult(TaskStatus.COMPLETE, 'done', 1, 'test.png'))

    def fails(self):
        raise RecordingError('decode failed')

    monkeypatch.setattr(test.recording, 'stop', fails)
    assert not test.action.run(test.context, test.args)
    assert test.result()['result']['status'] == 'failed'
    assert test.result()['result']['completed_rounds'] == 1


def test_rerunning_same_task_preserves_both_result_records(recorded_action, monkeypatch, tmp_path):
    test = recorded_action
    monkeypatch.setattr(test.action, 'execute', lambda *args, **kwargs:
                        TaskResult(TaskStatus.COMPLETE, 'done'))
    assert test.action.run(test.context, test.args)
    first = test.action.session
    assert test.action.run(test.context, test.args)
    assert test.action.session != first
    records = list((tmp_path / 'daily/results').glob('*.json'))
    assert len(records) == 2
    assert {json.loads(p.read_text(encoding='utf-8'))['session'] for p in records} == {first, test.action.session}
