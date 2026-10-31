from datetime import datetime
from types import SimpleNamespace

import pytest

from daily import park
from daily.calendar import DailyAttempts, DailyUnavailable
from daily.contracts import TaskConfig, TaskStatus
from daily.navigation import NavigationError
from daily.park import GIFT_PLACES, LOTTERY_PLACE, ParkRunner, ParkView


def runner_for(tmp_path, monkeypatch, kinds):
    elapsed, events = [0.0], []
    monkeypatch.setattr(park.time, 'monotonic', lambda: elapsed[0])
    def pause(seconds):
        elapsed[0] += seconds
    inputs = SimpleNamespace(key=lambda *args: events.append(('key', args)),
                             click=lambda *args: events.append(('click', args)), pause=pause)
    observation = SimpleNamespace(scene='home')
    nav = SimpleNamespace(inputs=inputs, observe=lambda: observation,
                          save=lambda *args: 'reward.png')
    nav.daily_attempts = DailyAttempts(tmp_path, clock=lambda: datetime.fromisoformat('2026-11-01T05:00:00+08:00'))
    runner = ParkRunner(nav)
    sequence = iter(kinds)
    last = [None]
    def read(*args):
        value = next(sequence, last[0])
        last[0] = value
        if isinstance(value, Exception):
            raise value
        kind, signature = value if isinstance(value, tuple) else (value, '')
        return ParkView(kind, observation, (590, 620, 90, 30), signature)
    runner.read = read
    return runner, events


def test_failed_attempt_never_repeats_its_first_e_after_restart(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry', RuntimeError('lost capture')])
    with pytest.raises(RuntimeError, match='lost capture'):
        runner.consume(GIFT_PLACES[0])
    assert events == [('key', (0x45, .12))]
    runner.read = lambda *args: ParkView('entry', None)
    with pytest.raises(DailyUnavailable):
        runner.consume(GIFT_PLACES[0])
    assert len(events) == 1


def test_recording_failure_blocks_reservation_and_first_e(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry'])
    def fail():
        raise RuntimeError('recording stopped')
    runner.nav.daily_attempts.guard = fail
    with pytest.raises(RuntimeError, match='recording stopped'):
        runner.consume(LOTTERY_PLACE)
    assert events == []
    assert not list(tmp_path.rglob('*.json'))


def test_lottery_scratch_is_clicked_once_and_get_precedes_success(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch,
        ['entry', 'scratch', 'scratch', 'scratch', 'reward', 'entry', 'entry', 'entry', 'entry'])
    assert runner.consume(LOTTERY_PLACE) == 'reward.png'
    assert events == [('key', (0x45, .12)), ('click', (635, 635)), ('click', (635, 635))]
    record = next(tmp_path.rglob('lottery.json')).read_text()
    assert '"state": "complete"' in record


def test_unchanged_dialogue_has_one_e_and_then_stops(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry', ('dialogue', 'same line')])
    with pytest.raises(NavigationError, match='GET'):
        runner.consume(GIFT_PLACES[1])
    assert events == [('key', (0x45, .12)), ('key', (0x45, .12))]
    assert '"state": "failed"' in next(tmp_path.rglob('*.json')).read_text(encoding='utf-8')


def test_return_without_get_does_not_claim_completion(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry', 'park'])
    with pytest.raises(NavigationError, match='GET'):
        runner.consume(GIFT_PLACES[2])
    assert len(events) == 1


def test_entry_only_never_reserves_or_interacts(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.entry = lambda *args, **kwargs: (LOTTERY_PLACE, ParkView('entry', None))
    result = runner.run(TaskConfig.parse({'task': 'lottery', 'entry_only': True}))
    assert result.status == TaskStatus.ENTRY_VERIFIED
    assert events == []
    assert not list(tmp_path.rglob('*.json'))


def test_pending_progress_starts_only_after_attempt_reservation(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry'])
    def before_input():
        assert next(tmp_path.rglob('lottery.json')).exists()
        assert not events
        raise RuntimeError('cancelled before E')
    with pytest.raises(RuntimeError, match='cancelled'):
        runner.consume(LOTTERY_PLACE, before_input)
    assert events == []
