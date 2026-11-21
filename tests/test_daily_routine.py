from types import SimpleNamespace

import pytest

from daily import actions, live
from daily.calendar import DailyAttempts
from daily.contracts import ResourceObservation, TaskConfig, TaskResult, TaskStatus


@pytest.mark.parametrize('resume_steps', [None, ['holowork', 'park_gifts', 'cooking']])
def test_routine_handles_only_the_requested_tasks(tmp_path, monkeypatch, resume_steps):
    tasks = []
    ledger = DailyAttempts(tmp_path)
    ledger.reserve('lottery').finish(evidence='lottery.png')
    nav = SimpleNamespace(daily_attempts=ledger, recording=None,
                          observe=lambda: SimpleNamespace(scene='notifications'),
                          inputs=SimpleNamespace(check=lambda: None),
                          to_daily_missions=lambda: tasks.append('missions'),
                          to_home=lambda: tasks.append('home'))
    action = actions.RunDailyTask()

    def execute(navigator, options, *, progress):
        tasks.append(options.task)
        assert options.strategy == 'aggressive'
        keys = next(keys for task, _, keys in actions.DAILY_STEPS if task == options.task)
        for key in keys:
            ledger.reserve(key).finish(evidence=f'{key}.png')
        return TaskResult(TaskStatus.COMPLETE, 'done')

    class Cooking:
        def __init__(self, *args):
            pass

        def run(self, config, *, required_cost, daily_ledger):
            assert (config.task, config.rounds, config.multiplier, config.resource_budget) == ('cooking', 3, 1, 90)
            assert required_cost == 30 and daily_ledger is ledger
            tasks.append('cooking')
            return TaskResult(TaskStatus.COMPLETE, 'done', 3)

    class Live:
        def __init__(self, *args):
            pass

        def run(self, config, *, drain):
            assert config.task == 'solo_live' and config.resource_budget == 10 and drain
            tasks.append('solo_live')
            return TaskResult(TaskStatus.COMPLETE, 'done', 1)

    class Rewards:
        def __init__(self, *args):
            pass

        def run(self, *, missions_and_gifts_only):
            assert missions_and_gifts_only
            tasks.append('rewards')
            return TaskResult(TaskStatus.COMPLETE, 'done', 1)

    monkeypatch.setattr(action, 'execute', execute)
    monkeypatch.setattr(actions, 'CookingRunner', Cooking)
    monkeypatch.setattr(actions, 'SoloLiveRunner', Live)
    monkeypatch.setattr(actions, 'RewardsRunner', Rewards)
    result = action.run_routine(nav, TaskConfig('daily_routine', strategy='aggressive'), lambda _: None,
                                resume_steps=resume_steps)
    assert result.status == TaskStatus.COMPLETE
    if resume_steps:
        assert result.completed_rounds == 3
        assert tasks == ['holowork', 'park_gifts', 'cooking', 'home']
    else:
        assert result.completed_rounds == 7
        assert tasks == ['holowork', 'park_gifts', 'high_low', 'cooking', 'solo_live', 'missions', 'rewards', 'home']


@pytest.mark.parametrize(('observed', 'expected_multipliers', 'status'), [
    ([10, 5, 5, 0], [5, 5], TaskStatus.COMPLETE),
    ([8, 3, 3, 0], [5, 3], TaskStatus.COMPLETE),
    ([5, 0], [5], TaskStatus.COMPLETE),
    ([0], [], TaskStatus.COMPLETE),
    ([5, 5], [5], TaskStatus.SKIPPED),
    ([10, 6, 6], [5], TaskStatus.SKIPPED),
    ([11], [], TaskStatus.SKIPPED),
])
def test_daily_solo_uses_current_balance_and_requires_observed_zero(
        monkeypatch, observed, expected_multipliers, status):
    setup = SimpleNamespace(scene='solo_setup')
    nav = SimpleNamespace(to_solo_setup=lambda: setup, save=lambda *_: 'proof.png')
    runner = live.SoloLiveRunner(nav)
    balances = iter(observed)
    current = None

    def stable_balance(_):
        nonlocal current
        current = ResourceObservation(next(balances), max(10, observed[0]))
        return setup, current

    runner.stable_balance = stable_balance
    runner.wait_auto_result = lambda: object()
    runner.finish_result = lambda _: setup
    multipliers = []

    class Energy:
        def __init__(self, nav):
            pass

        def configure(self, task, multiplier, budget, *, required_cost):
            assert task == 'solo_live' and required_cost == multiplier <= budget
            multipliers.append(multiplier)

    monkeypatch.setattr(live, 'EnergySettings', Energy)
    runner.controls.start = lambda config, *args, **kwargs: SimpleNamespace(
        effective_cost=config.multiplier, resource=current)
    result = runner.run(TaskConfig('solo_live', rounds=10, resource_budget=10), drain=True)
    assert result.status == status
    assert multipliers == expected_multipliers
