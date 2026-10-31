import json
from types import SimpleNamespace

import pytest

from daily import actions
from daily.contracts import TaskResult, TaskStatus
from daily.recording import RecordingError


@pytest.fixture
def recorded_action(monkeypatch, tmp_path):
    events = []

    class Recording:
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
    monkeypatch.setattr(actions, 'GameRecording', Recording)
    monkeypatch.setattr(actions, 'Navigator', Navigator)
    monkeypatch.setattr(actions, 'read_settings', lambda *args: {'task': 'connect'})
    monkeypatch.setattr(actions, 'recording_directory', lambda *args: tmp_path)
    action = actions.RunDailyTask()
    return SimpleNamespace(action=action, events=events, recording=Recording,
                           context=SimpleNamespace(tasker=SimpleNamespace(controller=object())),
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


def test_release_and_recording_finish_follow_task_result(recorded_action, monkeypatch):
    test = recorded_action
    monkeypatch.setattr(test.action, 'execute', lambda *args, **kwargs:
                        TaskResult(TaskStatus.COMPLETE, 'done', evidence='test.png'))
    assert test.action.run(test.context, test.args)
    assert test.events.index('start') < test.events.index('navigator')
    assert test.events.index('release') < test.events.index('task_end') < test.events.index('stop')
    assert test.result()['recording']['video'] == 'test.avi'


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
