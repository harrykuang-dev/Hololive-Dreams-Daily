"""Validated settings and results shared by pipelines and isolated workers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
import re
import time


class ConfigurationError(ValueError):
    pass


class TaskStatus(StrEnum):
    RUNNING = 'running'
    COMPLETE = 'complete'
    SKIPPED = 'skipped'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    ENTRY_VERIFIED = 'entry_verified'
    NAVIGATION_VERIFIED = 'navigation_verified'


TASKS = ('connect', 'holowork', 'park_gifts', 'lottery', 'cooking',
         'solo_live', 'high_low', 'jump_rope', 'multi_live', 'fishing', 'rewards')
USER_ENTERED_ROUNDS = frozenset(('cooking', 'solo_live', 'multi_live'))


def integer_in_range(value, label: str, *, minimum: int = 1, maximum: int = 999) -> int:
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value).strip()):
        raise ConfigurationError(f'{label}：請填入整數')
    number = int(str(value).strip())
    if not minimum <= number <= maximum:
        raise ConfigurationError(f'{label}：必須介於 {minimum} 至 {maximum}')
    return number


@dataclass(frozen=True)
class TaskConfig:
    task: str
    rounds: int = 1
    multiplier: int = 1
    entry_only: bool = False
    resource_budget: int | None = None
    target: int | None = None
    score_limit: int | None = None
    song: str = ''
    difficulty: str = 'current'
    team: str = 'current'

    @classmethod
    def parse(cls, data: dict) -> 'TaskConfig':
        task = data.get('task')
        if task not in TASKS:
            raise ConfigurationError('未知任務')
        rounds = data.get('rounds', '' if task in USER_ENTERED_ROUNDS else 1)
        rounds = integer_in_range(rounds, '局數')
        multiplier = integer_in_range(data.get('multiplier', 1), '倍率', maximum=99,
                                      minimum=0 if task in ('cooking', 'jump_rope') else 1)
        def optional_number(key, *, minimum=1):
            value = data.get(key)
            return None if value in (None, '') else integer_in_range(value, key, minimum=minimum, maximum=10**9)
        entry = data.get('entry_only', False)
        if entry not in (True, False, 'true', 'false'):
            raise ConfigurationError('入口驗證選項無效')
        return cls(task=task, rounds=rounds, multiplier=multiplier,
                   entry_only=entry is True or entry == 'true',
                   resource_budget=optional_number('resource_budget', minimum=0),
                   target=optional_number('target'), score_limit=optional_number('score_limit'),
                   song=str(data.get('song', '')), difficulty=str(data.get('difficulty', 'current')),
                   team=str(data.get('team', 'current')))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ResourceObservation:
    available: int | None
    capacity: int | None

    def permits(self, cost: int, budget_remaining: int | None = None) -> bool:
        # Account capacity is observed, never inferred from a subscription label.
        if type(cost) is not int or cost < 0:
            return False
        if type(self.available) is not int or type(self.capacity) is not int:
            return False
        if not 0 <= self.available <= self.capacity:
            return False
        return cost <= self.available and (budget_remaining is None or cost <= budget_remaining)


@dataclass(frozen=True)
class AutoObservation:
    enabled: bool | None
    remaining: int | None
    scene: str
    captured_at: float

    def permits_start(self, *, now: float | None = None) -> bool:
        age = (time.monotonic() if now is None else now) - self.captured_at
        return (self.enabled is True and type(self.remaining) is int and self.remaining > 0
                and self.scene in ('solo_setup', 'multi_ready')
                and math.isfinite(age) and 0 <= age <= 2.0)


@dataclass(frozen=True)
class TaskResult:
    status: TaskStatus
    reason: str
    completed_rounds: int = 0
    evidence: str | None = None
    pending_round: bool = False

    def to_dict(self):
        return asdict(self)

