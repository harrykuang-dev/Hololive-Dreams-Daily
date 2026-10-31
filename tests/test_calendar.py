from datetime import datetime
import json

import pytest

from daily.calendar import DailyAttempts, DailyUnavailable, game_day


def moment(value):
    return datetime.fromisoformat(value)


def test_game_day_changes_at_four_in_utc_plus_eight():
    assert game_day(moment('2026-11-01T03:59:59+08:00')) == '2026-10-31'
    assert game_day(moment('2026-11-01T04:00:00+08:00')) == '2026-11-01'
    assert game_day(moment('2026-10-31T20:00:00+00:00')) == '2026-11-01'


def test_once_only_test_waits_until_five_minutes_after_reset(tmp_path):
    now = [moment('2026-11-01T04:04:59+08:00')]
    ledger = DailyAttempts(tmp_path, not_before='2026-11-01T04:05:00+08:00', clock=lambda: now[0])
    with pytest.raises(DailyUnavailable, match='04:05'):
        ledger.reserve('lottery')
    assert not list(tmp_path.rglob('*.json'))
    now[0] = moment('2026-11-01T04:05:00+08:00')
    attempt = ledger.reserve('lottery')
    assert attempt.record['game_day'] == '2026-11-01'


def test_pending_or_failed_attempt_blocks_replay_even_after_restart(tmp_path):
    clock = lambda: moment('2026-11-01T05:00:00+08:00')
    ledger = DailyAttempts(tmp_path, clock=clock)
    attempt = ledger.reserve('park_gift_holo_house')
    other = DailyAttempts(tmp_path, clock=clock)
    with pytest.raises(DailyUnavailable):
        other.reserve('park_gift_holo_house')
    attempt.finish(error='lost result')
    with pytest.raises(DailyUnavailable):
        other.reserve('park_gift_holo_house')
    assert json.loads(attempt.path.read_text())['state'] == 'failed'


def test_new_game_day_allows_a_new_attempt_but_other_places_are_independent(tmp_path):
    now = [moment('2026-11-01T05:00:00+08:00')]
    ledger = DailyAttempts(tmp_path, clock=lambda: now[0])
    first = ledger.reserve('park_gift_holo_house')
    first.finish(evidence='first.png')
    ledger.reserve('park_gift_the_cell')
    now[0] = moment('2026-11-02T04:05:00+08:00')
    second = ledger.reserve('park_gift_holo_house')
    assert second.path != first.path
    assert first.record['state'] == 'complete'


def test_recording_guard_failure_neither_reserves_nor_authorizes_an_attempt(tmp_path):
    def failed():
        raise RuntimeError('recording lost')

    ledger = DailyAttempts(tmp_path, guard=failed)
    with pytest.raises(RuntimeError, match='recording lost'):
        ledger.reserve('lottery')
    assert not list(tmp_path.rglob('*.json'))


def test_corrupt_attempt_still_blocks_replay(tmp_path):
    ledger = DailyAttempts(tmp_path, clock=lambda: moment('2026-11-01T05:00:00+08:00'))
    attempt = ledger.reserve('lottery')
    attempt.path.write_text('interrupted write')
    with pytest.raises(DailyUnavailable):
        ledger.reserve('lottery')


def recorded_attempt(tmp_path):
    video, timeline = tmp_path / 'original.avi', tmp_path / 'events.jsonl'
    video.write_bytes(b'test recording')
    timeline.write_text('\n'.join(json.dumps(row) for row in (
        {'event': 'recording_ready'}, {'event': 'key_down', 'key': 0x45},
        {'event': 'recording_stopped'}))+'\n')
    ledger = DailyAttempts(tmp_path / 'ledger', clock=lambda: moment('2026-11-01T05:00:00+08:00'),
                           recording=lambda: {'video': str(video), 'timeline': str(timeline)})
    return ledger, ledger.reserve('lottery'), timeline


def test_recorded_open_dialogue_can_resume_once_without_a_new_daily_reservation(tmp_path):
    ledger, original, _ = recorded_attempt(tmp_path)
    original.finish(error='unknown speaker')
    recovery = ledger.resume_open_dialogue('lottery')
    assert recovery.record['prior_outcome']['state'] == 'failed'
    recovery.finish(evidence='received.png')
    assert recovery.record['state'] == 'complete'
    with pytest.raises(DailyUnavailable):
        ledger.resume_open_dialogue('lottery')
    with pytest.raises(DailyUnavailable):
        ledger.reserve('lottery')


def test_recovery_requires_original_first_e_and_live_recording_guard(tmp_path):
    ledger, original, timeline = recorded_attempt(tmp_path)
    original.finish(error='unknown speaker')
    timeline.write_text('')
    with pytest.raises(DailyUnavailable):
        ledger.resume_open_dialogue('lottery')
    assert not original.path.with_suffix('.recovery').exists()
    def failed():
        raise RuntimeError('recording lost')
    ledger.guard = failed
    with pytest.raises(RuntimeError, match='recording lost'):
        ledger.resume_open_dialogue('lottery')


def test_recovery_does_not_compete_with_a_pending_or_unfinalized_original_run(tmp_path):
    ledger, original, timeline = recorded_attempt(tmp_path)
    with pytest.raises(DailyUnavailable):
        ledger.resume_open_dialogue('lottery')
    original.finish(error='lost result')
    timeline.write_text('\n'.join(json.dumps(row) for row in (
        {'event': 'recording_ready'}, {'event': 'key_down', 'key': 0x45}))+'\n')
    with pytest.raises(DailyUnavailable):
        ledger.resume_open_dialogue('lottery')
    assert not original.path.with_suffix('.recovery').exists()


def test_attempted_task_snapshot_includes_corrupt_records_and_changes_with_game_day(tmp_path):
    now = [moment('2026-11-01T05:00:00+08:00')]
    ledger = DailyAttempts(tmp_path, clock=lambda: now[0])
    first = ledger.reserve('park_gift_holo_house')
    first.path.write_text('interrupted')
    assert ledger.attempted_tasks() == {'park_gift_holo_house'}
    now[0] = moment('2026-11-02T04:05:00+08:00')
    assert ledger.attempted_tasks() == set()
