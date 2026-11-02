import pytest

from daily.auto_deadline import AutoDeadline, CountdownObservation


def test_stopped_capture_cannot_outlive_initial_countdown_margin():
    guard = AutoDeadline(100)
    assert not guard.expired(124.99)
    assert guard.expired(125)


@pytest.mark.parametrize('scene', ['multi_matching', 'multi_song_select', 'multi_waiting'])
def test_only_fresh_pre_ready_evidence_extends_the_window(scene):
    guard = AutoDeadline(100)
    assert guard.observe(CountdownObservation(scene, 110), now=110.5)
    assert not guard.expired(134.9) and guard.expired(135)
    assert not guard.observe(CountdownObservation(scene, 134), now=138)
    assert guard.expired(138)


@pytest.mark.parametrize('scene', ['unknown', 'home', 'solo_setup', 'live_playing'])
def test_unproven_scene_does_not_postpone_closing(scene):
    guard = AutoDeadline(100)
    assert not guard.observe(CountdownObservation(scene, 120, True, 100), now=120)
    assert guard.expired(125)


def test_ready_timer_shortens_deadline_and_cannot_extend_it():
    guard = AutoDeadline(100)
    guard.observe(CountdownObservation('multi_ready', 110, False, 0, 9), now=110)
    assert guard.deadline == 114
    guard.observe(CountdownObservation('multi_ready', 111, False, 0, 30), now=111)
    assert guard.deadline == 114 and guard.expired(114)


@pytest.mark.parametrize('scene', ['multi_matching', 'multi_song_select', 'multi_waiting'])
def test_ready_cannot_regress_and_extend_deadline(scene):
    guard = AutoDeadline(100)
    guard.observe(CountdownObservation('multi_ready', 110, False, 0, 9), now=110)
    assert not guard.observe(CountdownObservation(scene, 111), now=111)
    assert guard.deadline == 114 and guard.expired(114)


@pytest.mark.parametrize('enabled,count', [(False, 5), (None, 5), (True, 0), (True, None), (True, True)])
def test_unknown_or_exhausted_auto_never_disarms_guard(enabled, count):
    guard = AutoDeadline(100)
    guard.observe(CountdownObservation('multi_ready', 110, enabled, count, 5, True), now=110)
    assert not guard.auto_verified and guard.expired(110)


def test_fresh_positive_actual_auto_disarms_for_this_round_only():
    guard = AutoDeadline(100)
    assert guard.observe(CountdownObservation('multi_ready', 110, True, 3, 2, True), now=110.1)
    assert guard.auto_verified and not guard.expired(10000)
    assert AutoDeadline(10000).expired(10025)


def test_expired_deadline_cannot_be_revived_by_a_late_auto_read():
    guard = AutoDeadline(100)
    assert not guard.observe(CountdownObservation('multi_ready', 125, True, 3, start_authorized=True), now=125)
    assert guard.expired(125)


def test_auto_seen_during_preparation_does_not_disarm_final_start_guard():
    guard = AutoDeadline(100)
    guard.observe(CountdownObservation('multi_ready', 110, True, 3, 20), now=110)
    assert not guard.auto_verified
    guard.observe(CountdownObservation('multi_ready', 111, False, 3, 19), now=111)
    assert guard.expired(125)


def test_fresh_but_pre_arm_auto_proof_cannot_release_watchdog():
    guard = AutoDeadline(100)
    old = CountdownObservation('multi_ready', 99.5, True, 3, 30, True)
    assert not guard.observe(old, now=100.5)
    assert not guard.auto_verified and guard.expired(125)


@pytest.mark.parametrize('timestamp', [float('nan'), float('inf'), 121, 99, True])
def test_invalid_future_or_out_of_order_capture_cannot_extend_guard(timestamp):
    guard = AutoDeadline(100)
    assert not guard.observe(CountdownObservation('multi_matching', timestamp), now=120)
    assert guard.expired(125)
