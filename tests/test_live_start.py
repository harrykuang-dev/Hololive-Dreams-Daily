from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from daily.contracts import AutoObservation, ResourceObservation, TaskConfig
from daily.live import LiveBlocked, LiveControls, LiveState, parse_consumption


def controls(state):
    calls = []
    observation = SimpleNamespace(scene='solo_setup')
    nav = SimpleNamespace(observe=lambda: observation,
                          text=lambda *a: [SimpleNamespace(box=(1100, 630, 100, 40))],
                          inputs=SimpleNamespace(click=lambda *xy: calls.append(xy)))
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


@pytest.mark.parametrize('text,cost', [('×5×24', 5), ('×10 | ×48', 10), ('x1', 1),
    ('×5/10', None), ('AUTO5', None), ('×5×2×4', None), ('', None)])
def test_read_cost_without_confusing_the_reward_multiplier(text, cost):
    assert parse_consumption(text) == cost
