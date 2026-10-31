from types import SimpleNamespace

from daily.navigation import Navigator
from daily.vision import Match


def navigator(clock=(590, 182, 60, 58), shop=(536, 263, 58, 57)):
    nav = object.__new__(Navigator)
    anchors = {'map_clock': {'roi': [365, 114, 60, 58]},
               'map_shop': {'roi': [275, 249, 58, 57]},
               'map_fishing_hook': {'roi': [330, 433, 55, 58]}}
    matches = {'map_clock': [Match('clock', .95, clock)],
               'map_shop': [Match('shop', .96, shop)], 'map_fishing_hook': []}
    nav.vision = SimpleNamespace(anchors=anchors, find_all=lambda _, name, **kw: matches[name])
    return nav, matches


def test_player_occlusion_uses_two_landmarks_without_authorizing_teleport():
    nav, _ = navigator()
    candidates = nav.fishing_candidates(None)
    assert len(candidates) == 1
    assert candidates[0].name == 'map_lami_candidate'
    x, y, w, h = candidates[0].box
    assert 580 < x+w/2 < 615 and 390 < y+h/2 < 420


def test_ambiguous_landmark_cannot_produce_destination():
    nav, matches = navigator()
    matches['map_clock'] *= 2
    assert not nav.fishing_candidates(None)


def test_inconsistent_map_geometry_cannot_produce_destination():
    nav, _ = navigator(shop=(400, 263, 58, 57))
    assert not nav.fishing_candidates(None)
