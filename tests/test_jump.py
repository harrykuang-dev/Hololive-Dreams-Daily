from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from daily.jump import JumpRunner
from daily.navigation import NavigationError
from daily.resources import EnergySelection, ResourceUnavailable
from daily.contracts import ResourceObservation
from daily.minigames import start_minigame


def preflight(tmp_path, *, balance=30, capacity=100, cost=3, age=0, scene='jump_setup'):
    events = []
    observation = SimpleNamespace(scene=scene, captured_at=time.monotonic()-age)
    def text(observation, expected, roi):
        if expected == '^遊玩$':
            return [SimpleNamespace(box=(1000, 620, 200, 65))]
        if expected == '.*':
            return [SimpleNamespace(text=f'×{cost}', box=(850, 590, 60, 30))]
        return [SimpleNamespace(text=f'{balance}/{capacity}')]
    nav = SimpleNamespace(observe=lambda: observation, text=text,
        inputs=SimpleNamespace(click=lambda *a: events.append('click')),
        recording=SimpleNamespace(mark=lambda *a, **k: events.append('audit')))
    runner = JumpRunner(nav, tmp_path, tmp_path)
    energy = EnergySelection(1, 3, ResourceObservation(30, 100))
    return runner, energy, events


@pytest.mark.parametrize('values,budget', [({'age':3},3), ({'scene':'home'},3),
    ({'balance':2},3), ({'cost':6},6), ({},2)])
def test_ready_jump_cannot_start_with_stale_wrong_or_unaffordable_setup(tmp_path, values, budget):
    runner, energy, events = preflight(tmp_path, **values)
    with pytest.raises(NavigationError):
        runner.start_round({'phase':'capture_ready'}, energy, budget)
    assert not events


def test_jump_requires_capture_readiness_before_parent_start(tmp_path):
    runner, energy, events = preflight(tmp_path)
    with pytest.raises(NavigationError, match='擷取就緒'):
        runner.start_round({'phase':'loading'}, energy, 3)
    assert not events


def test_live_cannot_use_the_shared_minigame_start_boundary(tmp_path):
    runner, energy, events = preflight(tmp_path)
    with pytest.raises(NavigationError):
        start_minigame(runner.nav, 'solo_live', {'phase':'capture_ready'}, energy, 3)
    assert not events


def test_jump_audits_fresh_cost_before_pending_progress_and_only_start_click(tmp_path):
    runner, energy, events = preflight(tmp_path)
    metadata = runner.start_round({'phase':'capture_ready'}, energy, 3,
                                  lambda: events.append('pending'))
    assert events == ['audit', 'pending', 'click']
    assert metadata['cost'] == 3 and metadata['resource']['available'] == 30


@pytest.mark.parametrize('balance,allowed', [(0,True), (1,False)])
def test_zero_cost_jump_authorization_requires_fresh_zero_balance(tmp_path, balance, allowed):
    runner, energy, events = preflight(tmp_path, balance=balance, cost=0)
    energy = replace(energy, resource=ResourceObservation(0,100), empty_energy_play=True)
    if allowed:
        assert runner.start_round({'phase':'capture_ready'}, energy, 0)['cost'] == 0
        assert events == ['audit', 'click']
    else:
        with pytest.raises(ResourceUnavailable):
            runner.start_round({'phase':'capture_ready'}, energy, 0)
        assert not events


@pytest.mark.parametrize('balance', [0, 53, 180])
def test_explicit_zero_multiplier_permits_free_play_with_known_energy(tmp_path, balance):
    runner, energy, events = preflight(tmp_path, balance=balance, capacity=200, cost=0)
    energy = EnergySelection(0, 0, ResourceObservation(balance, 200))
    metadata = runner.start_round({'phase':'capture_ready'}, energy, 0)
    assert metadata['cost'] == 0 and metadata['resource']['available'] == balance
    assert events == ['audit', 'click']


@pytest.mark.parametrize('labels,allowed', [(['ō本次跳躍次數','獲得獎勵'],True),
    (['獲得獎勵'],False), (['本次跳躍次數','本次跳躍次數','獲得獎勵'],False)])
def test_reward_ok_requires_both_unique_jump_summary_labels(tmp_path, labels, allowed):
    runner, _, _ = preflight(tmp_path)
    def text(observation, expected, roi):
        if '本次跳躍次數' in expected:
            return [SimpleNamespace(text=value) for value in labels]
        return [SimpleNamespace(box=(1060,640,55,25))]
    runner.nav.text = text
    assert (runner.reward_button(SimpleNamespace(scene='jump_result')) is not None) == allowed
    assert runner.reward_button(SimpleNamespace(scene='solo_setup')) is None


def test_existing_jump_reward_recovery_closes_ok_without_next_or_new_worker(tmp_path):
    runner, _, events = preflight(tmp_path)
    runner.nav.observe = lambda: SimpleNamespace(scene='jump_result')
    runner.reward_button = lambda observation: (1060,640,55,25)
    runner.nav.wait_for = lambda scenes, **kwargs: 'jump_setup'
    runner.result_page = lambda: pytest.fail('Existing reward must not repeat Next')
    runner.finish_event = lambda: 'jump_setup'
    assert runner.finish_result() == 'jump_setup'
    assert events == ['click']


@pytest.mark.parametrize('scene', ['jump_setup', 'home'])
def test_jump_event_completion_accepts_only_observed_final_destinations(tmp_path, scene):
    runner, _, events = preflight(tmp_path)
    runner.nav.observe = lambda: SimpleNamespace(scene=scene)
    runner.nav.wait_for = lambda scenes, **kwargs: scene if scene in scenes else pytest.fail('wrong destination')
    assert runner.finish_event() == scene
    assert not events
