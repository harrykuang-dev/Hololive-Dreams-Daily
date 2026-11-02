"""Same-frame resource and built-in Auto validation shared by Live gates."""
from dataclasses import dataclass, replace

from .contracts import AutoObservation, ResourceObservation, TaskConfig
from .navigation import NavigationError


class LiveBlocked(NavigationError):
    pass


@dataclass(frozen=True)
class LiveState:
    auto: AutoObservation
    resource: ResourceObservation
    cost: int | None
    effective_cost: int | None = None


@dataclass(frozen=True)
class LiveStartProof:
    config: TaskConfig
    state: LiveState
    budget_remaining: int | None
    energy: object
    empty_policy_verified: bool


def validate_live_start(config, state, budget_remaining=None, *, energy=None, now=None):
    expected = {'solo_live': 'solo_setup', 'multi_live': 'multi_ready'}.get(config.task)
    if config.entry_only or expected is None or state.auto.scene != expected:
        raise LiveBlocked('Live 開始畫面或入口驗證設定不符')
    if budget_remaining is None:
        budget_remaining = config.resource_budget
    if budget_remaining is not None and (type(budget_remaining) is not int or budget_remaining < 0):
        raise LiveBlocked('Live 資源預算無效')
    zero_energy = state.cost == 0 and state.resource.available == 0
    empty_policy = (energy is not None and energy.empty_energy_play
                    and energy.resource.available == state.resource.available == 0
                    and state.cost in {energy.cost, energy.effective_cost}
                    and energy.multiplier == config.multiplier)
    effective_cost = 0 if zero_energy or empty_policy else state.cost
    if (type(config.multiplier) is not int or config.multiplier < 1
            or type(state.cost) is not int or state.cost < 0
            or (state.cost != config.multiplier and not zero_energy)):
        raise LiveBlocked('Live 實際消耗量與使用者設定不符')
    if not state.resource.permits(effective_cost, budget_remaining):
        raise LiveBlocked('Live 現有能量或本次消耗上限不足')
    if not state.auto.permits_start(now=now):
        raise LiveBlocked('未確認遊戲內建 Auto 已開啟且仍有可用次數')
    return LiveStartProof(config, replace(state, effective_cost=effective_cost), budget_remaining,
                          energy, bool(empty_policy))
