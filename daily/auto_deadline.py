"""Fail-closed deadline before an uncancellable multiplayer countdown.

This policy does not issue input or terminate processes. A separate supervisor
must own the exact game process before matchmaking can be enabled.
"""
from dataclasses import dataclass
import math


PRE_READY_SCENES = frozenset({'multi_matching', 'multi_song_select', 'multi_waiting'})


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class CountdownObservation:
    scene: str
    captured_at: float
    auto_enabled: bool | None = None
    auto_remaining: int | None = None
    remaining_seconds: int | None = None
    start_authorized: bool = False


class AutoDeadline:
    """At most25s after a positively observed pre-ready frame without Auto.

    The game's difficulty timer is30s. Fresh pre-ready evidence can move the
    deadline, but unknown/stale frames cannot. On the ready page a readable
    timer can only shorten it. Verified Auto is latched for this one round.
    """
    WINDOW = 25.0
    MARGIN = 5
    FRESHNESS = 2.0

    def __init__(self, now):
        if not finite_number(now):
            raise ValueError('Invalid deadline clock')
        self.deadline = now+self.WINDOW
        self.last_capture = now
        self.auto_verified = False
        self.ready_seen = False

    def observe(self, observation: CountdownObservation, *, now):
        if (not finite_number(now) or not finite_number(observation.captured_at)
                or not 0 <= now-observation.captured_at <= self.FRESHNESS
                or observation.captured_at < self.last_capture):
            return False
        # Once a deadline has elapsed, late evidence cannot reverse the stop.
        if self.expired(now):
            return False
        if observation.scene in PRE_READY_SCENES:
            if self.ready_seen:
                return False
            self.last_capture = observation.captured_at
            self.deadline = observation.captured_at+self.WINDOW
            return True
        if observation.scene != 'multi_ready':
            return False
        self.ready_seen = True
        self.last_capture = observation.captured_at
        # Merely seeing Auto during preparation is not final authorization:
        # subsequent team/energy UI could change it. Only the final Live start
        # gate may authorize this one-round latch after all fresh checks.
        if (observation.start_authorized is True and observation.auto_enabled is True
                and type(observation.auto_remaining) is int
                and observation.auto_remaining > 0):
            self.auto_verified = True
            return True
        timer = observation.remaining_seconds
        if type(timer) is int and 0 <= timer <= 30:
            self.deadline = min(self.deadline, observation.captured_at+max(0, timer-self.MARGIN))
        return True

    def expired(self, now):
        return not self.auto_verified and (not finite_number(now) or now >= self.deadline)
