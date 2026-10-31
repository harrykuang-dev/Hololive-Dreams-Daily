from pathlib import Path

import cv2
import numpy as np
import pytest

from daily.vision import Match, Vision, parse_remaining

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('text,expected', [('還有 3K 次', 3000), ('1.5K', 1500),
    ('2萬', 20000), ('0', 0), ('AUTO', None), ('', None), ('10/10', None), ('-1', None)])
def test_auto_remaining_count(text, expected):
    assert parse_remaining(text) == expected


def test_unknown_frame_has_no_scene():
    vision = Vision(ROOT / 'resource')
    assert vision.scene(np.zeros((720, 1280, 3), dtype=np.uint8)) is None


def test_overlay_cannot_be_normalized_as_an_arbitrary_desktop():
    with pytest.raises(ValueError):
        Vision.normalize(np.zeros((1080, 1728, 3), dtype=np.uint8))


def test_notification_overlay_takes_precedence_over_unchanged_home_toolbar():
    vision = object.__new__(Vision)
    vision.anchors = {'home': {}, 'notifications': {'priority': 1}}
    vision.match = lambda frame, name: Match(name, .99, (0, 0, 10, 10))
    assert vision.scene(None) == 'notifications'
