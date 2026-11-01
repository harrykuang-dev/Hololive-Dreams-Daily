import pytest

from daily.contracts import (AutoObservation, ConfigurationError, ResourceObservation,
                             TaskConfig, TaskResult, TaskStatus)
from daily.journal import Journal


@pytest.mark.parametrize('task', ['cooking', 'solo_live', 'multi_live'])
@pytest.mark.parametrize('rounds', [None, '', ' ', 0, -1, '1.5', True, 'abc'])
def test_no_implicit_round_count(task, rounds):
    with pytest.raises(ConfigurationError):
        TaskConfig.parse({'task': task, 'rounds': rounds})


def test_independent_round_counts_and_paid_capacity():
    assert TaskConfig.parse({'task': 'cooking', 'rounds': '3'}).rounds == 3
    assert TaskConfig.parse({'task': 'solo_live', 'rounds': '2'}).rounds == 2
    assert TaskConfig.parse({'task': 'multi_live', 'rounds': '5'}).rounds == 5
    assert ResourceObservation(180, 200).permits(150)
    assert not ResourceObservation(80, 100).permits(150)
    assert not ResourceObservation(180, 200).permits(150, 100)
    assert not ResourceObservation(None, 200).permits(1)


def test_zero_resource_setting_does_not_allow_zero_rounds_or_targets():
    config = TaskConfig.parse({'task': 'solo_live', 'rounds': '1', 'multiplier': '1', 'resource_budget': '0'})
    assert config.multiplier == 1 and config.resource_budget == 0
    assert ResourceObservation(0, 20).permits(0, 0)
    assert not ResourceObservation(0, 20).permits(1, 0)
    assert not ResourceObservation(None, 20).permits(0, 0)
    for field in ('rounds', 'target', 'score_limit', 'multiplier'):
        with pytest.raises(ConfigurationError):
            TaskConfig.parse({'task': 'solo_live', 'rounds': '1', field: 0})


@pytest.mark.parametrize('task', ['cooking', 'jump_rope'])
def test_explicit_free_minigame_setting_keeps_rounds_positive(task):
    assert TaskConfig.parse({'task': task, 'rounds': '2', 'multiplier': '0'}).multiplier == 0
    for value in (-1, 100, True):
        with pytest.raises(ConfigurationError):
            TaskConfig.parse({'task': task, 'rounds': '2', 'multiplier': value})


@pytest.mark.parametrize('task', ['solo_live', 'multi_live'])
def test_live_does_not_inherit_the_minigame_zero_multiplier(task):
    with pytest.raises(ConfigurationError):
        TaskConfig.parse({'task': task, 'rounds': '1', 'multiplier': '0'})


@pytest.mark.parametrize('enabled,remaining,scene,captured', [
    (False, 3, 'solo_setup', 10), (None, 3, 'solo_setup', 10),
    (True, 0, 'solo_setup', 10), (True, None, 'multi_ready', 10),
    (True, 3, 'home', 10), (True, 3, 'solo_setup', 1),
    (True, 3, 'multi_ready', 20), (True, True, 'solo_setup', 10),
])
def test_auto_gate_denies_uncertain_exhausted_or_stale(enabled, remaining, scene, captured):
    assert not AutoObservation(enabled, remaining, scene, captured).permits_start(now=10)


def test_auto_gate_requires_current_positive_observation():
    assert AutoObservation(True, 1, 'solo_setup', 9.5).permits_start(now=10)
    assert AutoObservation(True, 100, 'multi_ready', 9.5).permits_start(now=10)


def test_entry_only_is_never_complete(tmp_path):
    journal = Journal(tmp_path)
    config = TaskConfig.parse({'task': 'lottery', 'entry_only': True})
    journal.record(config, TaskResult(TaskStatus.ENTRY_VERIFIED, '入口已驗證', evidence='frame.png'), session='run1')
    assert not journal.verified_complete(journal.previous('run1', 'lottery'))
    journal.record(config, TaskResult(TaskStatus.SKIPPED, '當日已使用'), session='run1')
    assert not journal.verified_complete(journal.previous('run1', 'lottery'))
    assert journal.previous('other', 'lottery') is None

