from types import SimpleNamespace

import pytest

from daily.fishing import FishingRunner
from daily.resources import ResourceUnavailable


def test_paid_or_unreadable_bait_never_starts_worker(tmp_path):
    nav = SimpleNamespace(observe=lambda: SimpleNamespace(scene='fishing_setup_second', image=None),
                          text=lambda *args, **kwargs: [],
                          vision=SimpleNamespace(match=lambda *args: None))
    runner = FishingRunner(nav, tmp_path, tmp_path,
                           supervisor=lambda *args, **kwargs: pytest.fail('must not launch'))
    with pytest.raises(ResourceUnavailable, match='無限練餌'):
        runner.run(SimpleNamespace(target=3))


def test_mouse_release_is_allowed_after_recording_failure(tmp_path):
    events = []
    nav = SimpleNamespace(controller=SimpleNamespace(post_touch_up=lambda contact: ('up', contact)),
                          inputs=SimpleNamespace(wait=lambda job: events.append(job)),
                          recording=SimpleNamespace(mark=lambda *args, **kwargs: None))
    FishingRunner(nav, tmp_path, tmp_path).release_mouse()
    assert events == [('up', 0)]
