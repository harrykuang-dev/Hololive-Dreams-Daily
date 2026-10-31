from types import SimpleNamespace

import numpy as np
import pytest

from daily.live import SoloLiveRunner
from daily.navigation import NavigationError


def result_navigation(monkeypatch, *, advances_after=None):
    clock, clicks = [0.0], []
    monkeypatch.setattr('daily.live.time.monotonic', lambda: clock[0])
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    result = SimpleNamespace(scene='live_result', image=image)
    portal = SimpleNamespace(scene='live_portal', image=image)
    nav = SimpleNamespace(
        observe=lambda: portal if advances_after is not None and len(clicks) >= advances_after else result,
        text=lambda *a, **k: [SimpleNamespace(box=(1100, 630, 100, 40))],
        save=lambda *a: 'result.png',
        inputs=SimpleNamespace(click=lambda *xy: clicks.append(xy),
                               pause=lambda seconds: clock.__setitem__(0, clock[0]+seconds)))
    return SoloLiveRunner(nav), result, clicks


def test_result_animation_consumes_first_next_but_fresh_stable_page_allows_one_retry(monkeypatch):
    runner, result, clicks = result_navigation(monkeypatch, advances_after=2)
    assert runner.finish_result(result).scene == 'live_portal'
    assert len(clicks) == 2


def test_no_progress_cannot_cause_unbounded_result_clicks(monkeypatch):
    runner, result, clicks = result_navigation(monkeypatch)
    with pytest.raises(NavigationError, match='未能穩定'):
        runner.finish_result(result)
    assert len(clicks) == 2
