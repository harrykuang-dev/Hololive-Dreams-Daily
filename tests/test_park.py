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


def test_transient_entry_animation_is_rechecked_before_reserving_or_pressing_e(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch,
        ['prompt_loading', 'park', 'entry', 'scratch', 'reward',
         'entry', 'entry', 'entry', 'entry'])
    assert runner.consume(LOTTERY_PLACE) == 'reward.png'
    assert events[0] == ('key', (0x45, .12))
    assert sum(event[0] == 'key' for event in events) == 1


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


def test_known_open_lottery_dialogue_resumes_without_entering_or_pressing_initial_e(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.read = lambda *args: ParkView('dialogue', None, signature='究竟會刮出什麼呢～要來玩一張看看嗎')
    finished = []
    runner.nav.daily_attempts.resume_open_dialogue = lambda task: SimpleNamespace(
        finish=lambda **kw: finished.append(kw))
    runner.finish_interaction = lambda place: 'received.png'
    runner.entry = lambda *args, **kw: pytest.fail('Must not reenter daily interaction')
    result = runner.run(TaskConfig.parse({'task': 'lottery', 'entry_only': False}))
    assert result.status == TaskStatus.COMPLETE and result.completed_rounds == 1
    assert events == [] and finished == [{'evidence': 'received.png'}]


def test_failed_gift_location_is_excluded_while_other_locations_remain_independent(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.nav.daily_attempts.reserve(GIFT_PLACES[0].key).finish(error='uncertain')
    consumed = []
    def entry(task, *, exclude):
        assert GIFT_PLACES[0].key in exclude
        if GIFT_PLACES[1].key not in exclude:
            return GIFT_PLACES[1], ParkView('entry', None)
        raise park.EntryUnavailable('No remaining notifications')
    runner.entry = entry
    runner.consume = lambda place, before_input: consumed.append(place.key) or 'received.png'
    result = runner.run(TaskConfig.parse({'task': 'park_gifts', 'entry_only': False}))
    assert result.completed_rounds == 1 and result.status == TaskStatus.COMPLETE
    assert consumed == [GIFT_PLACES[1].key] and events == []
