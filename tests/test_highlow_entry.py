from types import SimpleNamespace

import pytest

from daily.navigation import Navigator, NavigationError, Observation


def setup_entry(title_matches):
    nav = Navigator.__new__(Navigator)
    nav.to_home = lambda: None
    map_view = Observation(None, 'park_map', 0)
    nav.open_map = lambda: map_view
    nav.observe = lambda: map_view
    nav.wait_for = lambda scenes, **kwargs: Observation(None, next(iter(scenes)), 0)
    nav.save = lambda *args: 'card.png'
    clicks = []
    nav.inputs = SimpleNamespace(click=lambda *xy: clicks.append(xy), pause=lambda _: None)
    nav.vision = SimpleNamespace(find_all=lambda *args, **kwargs: [SimpleNamespace(box=(800, 200, 60, 60))],
                                match=lambda *args: True)
    def text(observation, expected, roi):
        hit = expected in ('電玩巨蛋城', '^移動$', '^遊玩區域$') or title_matches
        box = (270, 650, 100, 30) if expected == '^遊玩區域$' else (980, 510, 80, 30)
        return [SimpleNamespace(box=box)] if hit else []
    nav.text = text
    nav.approach = lambda prompt: ('entry', prompt)
    return nav, clicks


def test_recorded_title_and_description_are_required_before_teleport():
    nav, clicks = setup_entry(False)
    with pytest.raises(NavigationError, match='未找到'):
        nav.to_high_low_entry()
    assert (1020, 525) not in clicks  # Move was never clicked.


def test_entry_navigation_stops_at_prompt_without_opening_daily_game():
    nav, clicks = setup_entry(True)
    assert nav.to_high_low_entry() == ('entry', '^比大小$')
    assert clicks == [(320, 665), (994, 668), (830, 230), (1020, 525)]
    # The fake input surface has no key method: any E/game-start would fail.
