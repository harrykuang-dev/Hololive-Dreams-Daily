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
        received_reward_button=lambda observation: None,
        live_achievement_button=lambda observation: None,
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


def test_auto_result_reward_overlay_is_closed_once_before_next(monkeypatch):
    runner, result, clicks = result_navigation(monkeypatch)
    unknown = SimpleNamespace(scene=None, image=result.image)
    reward = SimpleNamespace(scene='holowork_get', image=result.image)
    portal = SimpleNamespace(scene='song_select', image=result.image)
    first = [True]
    def observe():
        if first[0]:
            first[0] = False
            return unknown
        return reward if not clicks else result if len(clicks) == 1 else portal
    runner.nav.observe = observe
    runner.nav.received_reward_button = lambda obs: (540, 620, 200, 30) if obs.scene == 'holowork_get' else None
    assert runner.finish_result(result).scene == 'song_select'
    assert clicks == [(640, 635), (1150, 650)]


def test_stuck_received_reward_is_not_repeatedly_closed(monkeypatch):
    runner, result, clicks = result_navigation(monkeypatch)
    reward = SimpleNamespace(scene='holowork_get', image=result.image)
    runner.nav.observe = lambda: reward
    runner.nav.received_reward_button = lambda obs: (540, 620, 200, 30)
    with pytest.raises(NavigationError, match='下一步'):
        runner.wait_next_button(reward)
    assert clicks == [(640, 635)]


def test_get_then_achievement_details_return_to_result_without_starting_another_song(monkeypatch):
    runner, result, clicks = result_navigation(monkeypatch)
    scenes = ['holowork_get', 'live_achievement', 'live_result', 'song_select']
    runner.nav.observe = lambda: SimpleNamespace(scene=scenes[min(len(clicks), 3)], image=result.image)
    runner.nav.received_reward_button = lambda obs: (540, 620, 200, 30) if obs.scene == 'holowork_get' else None
    runner.nav.live_achievement_button = lambda obs: (1015, 70, 60, 60) if obs.scene == 'live_achievement' else None
    assert runner.finish_result(result).scene == 'song_select'
    assert clicks == [(640, 635), (1045, 100), (1150, 650)]


@pytest.mark.parametrize('moving_control', [False, True])
def test_next_stability_observes_the_button_instead_of_animated_result_art(monkeypatch, moving_control):
    runner, result, clicks = result_navigation(monkeypatch)
    frames = [0]
    def observe():
        frames[0] += 1
        image = result.image.copy()
        image[180:580, 600:1050] = 240 if frames[0] % 2 else 20
        if moving_control:
            image[630:675, 1060:1200] = 240 if frames[0] % 2 else 20
        return SimpleNamespace(scene='live_result', image=image)
    runner.nav.observe = observe
    if moving_control:
        with pytest.raises(NavigationError, match='下一步'):
            runner.wait_next_button(observe())
    else:
        _, buttons = runner.wait_next_button(observe())
        assert len(buttons) == 1 and frames[0] < 12
    assert not clicks
