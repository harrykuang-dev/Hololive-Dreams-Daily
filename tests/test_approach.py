from types import SimpleNamespace

import pytest

from daily.navigation import Navigator, NavigationError


def navigator(scene='home', appears_after=None):
    nav = object.__new__(Navigator)
    calls = []
    nav.inputs = SimpleNamespace(key=lambda *args: calls.append(args), pause=lambda _: None)
    nav.observe = lambda: SimpleNamespace(scene=scene)
    nav.text = lambda *a, **k: ([object()] if appears_after is not None and len(calls)>=appears_after else [])
    nav.save = lambda *a: 'evidence.png'
    return nav, calls


def test_approach_stops_as_soon_as_prompt_is_visible():
    nav, calls = navigator(appears_after=2)
    nav.approach('開始釣魚')
    assert calls == [(0x57, .30), (0x57, .30)]


def test_missing_prompt_has_finite_movement_limit():
    nav, calls = navigator()
    with pytest.raises(NavigationError, match='有限移動'):
        nav.approach('開始釣魚', segments=3)
    assert len(calls) == 3


def test_approach_cannot_move_from_unknown_screen():
    nav, calls = navigator(scene=None)
    with pytest.raises(NavigationError, match='畫面已改變'):
        nav.approach('開始釣魚')
    assert not calls
