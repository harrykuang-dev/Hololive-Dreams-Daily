from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from daily.contracts import AutoObservation, ResourceObservation, TaskConfig
from daily.live import LiveBlocked, LiveControls, LiveState, parse_consumption
from daily.resources import EnergySelection, read_setup_cost


def controls(state):
    calls = []
    def click(*xy, before_post):
        before_post()
        calls.append(xy)
    observation = SimpleNamespace(scene='solo_setup')
    nav = SimpleNamespace(observe=lambda: observation,
                          text=lambda *a: [SimpleNamespace(box=(1100, 630, 100, 40))],
                          inputs=SimpleNamespace(click=click))
    control = LiveControls(nav)
    control.read = lambda ob: state
    return control, calls


def state(**auto):
    values = dict(enabled=True, remaining=1, scene='solo_setup', captured_at=time.monotonic())
    values.update(auto)
    return LiveState(AutoObservation(**values), ResourceObservation(10, 20), 1)


@pytest.mark.parametrize('auto', [dict(enabled=False), dict(enabled=None), dict(remaining=0),
    dict(remaining=None), dict(captured_at=0), dict(scene='live_portal')])
def test_actual_start_never_clicks_when_auto_is_off_empty_stale_or_unknown(auto):
    control, calls = controls(state(**auto))
    with pytest.raises(LiveBlocked):
        control.start(TaskConfig('solo_live', rounds=1))
    assert not calls


def test_actual_start_requires_both_energy_and_the_requested_cost():
    for snapshot in [replace(state(), resource=ResourceObservation(0, 20)), replace(state(), cost=5)]:
        control, calls = controls(snapshot)
        with pytest.raises(LiveBlocked):
            control.start(TaskConfig('solo_live', rounds=1, multiplier=1))
        assert not calls


def test_entry_only_cannot_start_even_with_positive_auto_and_energy():
    control, calls = controls(state())
    with pytest.raises(LiveBlocked):
        control.start(TaskConfig('solo_live', rounds=1, entry_only=True))
    assert not calls


def test_fresh_game_auto_allows_only_one_observed_decide_click():
    control, calls = controls(state())
    control.start(TaskConfig('solo_live', rounds=1, resource_budget=1))
    assert calls == [(1150, 650)]


def test_multiplayer_never_starts_without_independent_guard():
    control, calls = controls(state(scene='multi_ready'))
    with pytest.raises(LiveBlocked, match='監督'):
        control.start(TaskConfig('multi_live'))
    assert not calls


def test_solo_rechecks_snapshot_freshness_after_recording_audit(monkeypatch):
    now = [100.0]
    monkeypatch.setattr('daily.contracts.time.monotonic', lambda: now[0])
    control, calls = controls(state())
    def delayed_audit(*args, **kwargs):
        now[0] += 3
    control.nav.recording = SimpleNamespace(mark=delayed_audit)
    with pytest.raises(LiveBlocked, match='Auto'):
        control.start(TaskConfig('solo_live'))
    assert not calls


def test_guarded_multiplayer_runs_audit_and_final_check_before_input_then_releases():
    timeline = []
    snapshot = state(scene='multi_ready')
    control, _ = controls(snapshot)
    control.nav.observe = lambda: SimpleNamespace(scene='multi_ready')
    control.nav.recording = SimpleNamespace(mark=lambda *args, **kwargs: timeline.append('audit'))
    def click(x, y, *, before_post):
        timeline.append('cursor_audit')
        before_post()
        timeline.append('input_ack')
    control.nav.inputs.click = click
    token = object()
    class Supervisor:
        def prepare_start(self, config, read_state, budget, *, energy):
            assert read_state.auto is snapshot.auto and budget == 1
            timeline.append('full_gate')
            return token
        def perform_start(self, permit, action):
            assert permit is token
            action(lambda: timeline.append('final_check'))
            timeline.append('release_guard')
    control.start(TaskConfig('multi_live', resource_budget=1), auto_supervisor=Supervisor())
    assert timeline == ['audit', 'full_gate', 'cursor_audit', 'final_check', 'input_ack', 'release_guard']


@pytest.mark.parametrize('remaining', [0, 1])
def test_zero_energy_start_still_requires_auto_uses_and_explicit_zero_cost(remaining):
    control, calls = controls(replace(state(remaining=remaining), resource=ResourceObservation(0, 20), cost=0))
    config = TaskConfig('solo_live', rounds=1, multiplier=1, resource_budget=0)
    if remaining:
        control.start(config)
        assert calls == [(1150, 650)]
    else:
        with pytest.raises(LiveBlocked):
            control.start(config)
        assert not calls


def test_zero_cost_preflight_audit_is_written_before_the_only_start_click():
    control, calls = controls(replace(state(), resource=ResourceObservation(0, 20), cost=0))
    audit = []
    control.nav.recording = SimpleNamespace(mark=lambda name, **data: audit.append((name, data, list(calls))))
    control.start(TaskConfig('solo_live', rounds=1, multiplier=1, resource_budget=0))
    assert audit[0][0] == 'live_start_verified' and audit[0][2] == []
    assert audit[0][1]['cost'] == audit[0][1]['budget_remaining'] == 0
    assert audit[0][1]['resource']['available'] == 0
    assert calls == [(1150, 650)]


@pytest.mark.parametrize('available,policy,allowed', [(0, True, True), (0, False, False), (1, True, False)])
def test_empty_energy_nominal_cost_needs_observed_policy_and_fresh_zero_balance(available, policy, allowed):
    control, calls = controls(replace(state(), resource=ResourceObservation(available, 10), cost=1))
    energy = EnergySelection(1, 1, ResourceObservation(0, 10), empty_energy_play=policy)
    config = TaskConfig('solo_live', rounds=1, multiplier=1, resource_budget=0)
    if allowed:
        assert control.start(config, energy=energy).effective_cost == 0
        assert calls == [(1150, 650)]
    else:
        with pytest.raises(LiveBlocked):
            control.start(config, energy=energy)
        assert not calls


@pytest.mark.parametrize('text,cost', [('×5×24', 5), ('×10 | ×48', 10), ('x1', 1),
    ('×5/10', None), ('AUTO5', None), ('×5×2×4', None), ('', None)])
def test_read_cost_without_confusing_the_reward_multiplier(text, cost):
    assert parse_consumption(text) == cost


@pytest.mark.parametrize('texts,cost', [(['9'], 9), (['×9'], 9), (['0'], 0),
                                      (['×20'], 20), (['9', '31'], None), ([], None)])
def test_solo_cost_reads_only_the_confined_numeric_field(texts, cost):
    calls = []
    def text(observation, expected, roi):
        calls.append(roi)
        return [SimpleNamespace(text=value) for value in texts]
    nav = SimpleNamespace(text=text)
    assert read_setup_cost(nav, SimpleNamespace(scene='solo_setup')) == cost
    assert calls == [(840, 586, 72, 37)]


@pytest.mark.parametrize('labels,timer', [(['30'], 30), (['0'], 0), (['8'], 8),
    ([], None), (['30', '20'], None), (['31'], None), (['3K'], None), (['３０'], None)])
def test_countdown_uses_only_unique_confined_numeric_timer_without_authorizing_start(labels, timer):
    control, _ = controls(state(scene='multi_ready'))
    rois = []
    def text(observation, expected, roi):
        rois.append(roi)
        return [SimpleNamespace(text=value) for value in labels]
    control.nav.text = text
    result = control.read_countdown(SimpleNamespace(scene='multi_ready', captured_at=100))
    assert result.remaining_seconds == timer and result.captured_at == 100
    assert result.auto_enabled and not result.start_authorized
    assert rois == [(1170, 35, 100, 60)]


@pytest.mark.parametrize('displayed,budget,balance,policy,auto,allowed', [
    (5, 2, 2, True, True, True), (2, 2, 2, True, True, True),
    (5, 1, 2, True, True, False), (5, 2, 2, False, True, False),
    (5, 2, 3, True, True, False), (2, 2, 2, True, False, False)])
def test_partial_live_energy_never_bypasses_budget_fresh_balance_or_actual_auto(displayed, budget, balance, policy, auto, allowed):
    control, calls = controls(replace(state(enabled=auto), resource=ResourceObservation(balance, 10), cost=displayed))
    energy = EnergySelection(5, 5, ResourceObservation(2, 10), partial_energy_play=policy)
    config = TaskConfig('solo_live', multiplier=5, resource_budget=budget)
    if allowed:
        assert control.start(config, energy=energy).effective_cost == 2
        assert len(calls) == 1
    else:
        with pytest.raises(LiveBlocked): control.start(config, energy=energy)
        assert not calls


@pytest.mark.parametrize('snapshot', [EnergySelection(5, 5, ResourceObservation(2, 10), partial_energy_play=True),
                                    EnergySelection(5, 5, ResourceObservation(0, 20), empty_energy_play=True)])
def test_policy_snapshot_change_cannot_fall_back_to_the_legacy_zero_energy_gate(snapshot):
    control, calls = controls(replace(state(), resource=ResourceObservation(0, 10), cost=0))
    with pytest.raises(LiveBlocked, match='快照'):
        control.start(TaskConfig('solo_live', multiplier=5, resource_budget=0), energy=snapshot)
    assert not calls
