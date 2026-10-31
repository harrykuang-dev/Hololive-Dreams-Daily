from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from daily import navigation
from daily.navigation import EntryUnavailable, Navigator, Observation


class Capture:
    succeeded = True
    def wait(self): return self
    def get(self): return np.zeros((720, 1280, 3), dtype=np.uint8)


class Context:
    def __init__(self, words):
        self.tasker = SimpleNamespace(stopping=False,
                                      controller=SimpleNamespace(post_screencap=lambda: Capture()))
        self.words = words
        self.recognition_calls = []

    def run_recognition(self, name, image, override):
        self.recognition_calls.append((name, override))
        return SimpleNamespace(hit=True, filtered_results=[SimpleNamespace(text=w) for w in self.words])


def test_changing_park_backdrop_requires_multiple_toolbar_labels(tmp_path):
    root = Path(__file__).resolve().parents[1]
    context = Context(['遊戲', 'Live', '任務', '成就'])
    navigator = Navigator(context, root / 'resource', tmp_path)
    assert navigator.observe().scene == 'home'
    assert context.recognition_calls[0][1]['DailyOCR']['roi'] == [700, 15, 480, 100]


def test_one_generic_label_does_not_classify_unknown_scene(tmp_path):
    root = Path(__file__).resolve().parents[1]
    context = Context(['任務'])
    navigator = Navigator(context, root / 'resource', tmp_path)
    assert navigator.observe().scene is None


def test_holowork_header_ocr_handles_changing_dispatch_background(tmp_path):
    root = Path(__file__).resolve().parents[1]
    navigator = Navigator(Context(['holowork']), root / 'resource', tmp_path)
    assert navigator.observe().scene == 'holowork'


def test_main_menu_requires_multiple_distinct_menu_labels(tmp_path):
    root = Path(__file__).resolve().parents[1]
    navigator = Navigator(Context(['個人檔案', '會員資格', '持有物', '其他']), root / 'resource', tmp_path)
    assert navigator.observe().scene == 'main_menu'


def test_reward_overlay_requires_title_and_close_hint_on_any_background(tmp_path):
    root = Path(__file__).resolve().parents[1]
    navigator = Navigator(Context(['GET!', '點擊關閉']), root / 'resource', tmp_path)
    assert navigator.observe().scene == 'holowork_get'
    navigator = Navigator(Context(['GET!']), root / 'resource', tmp_path)
    assert navigator.observe().scene is None


def test_no_completed_work_notification_skips_without_guessing_another_route(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    navigator = Navigator(Context([]), root / 'resource', tmp_path)
    frames = iter([Observation(None, 'home', 0), Observation(None, 'notifications', 0)])
    navigator.observe = lambda: next(frames)
    navigator.to_home = lambda: None
    navigator.wait_for = lambda *args, **kwargs: None
    navigator.text = lambda *args, **kwargs: []
    navigator.save = lambda *args: 'notification.png'
    clicks = []
    navigator.inputs = SimpleNamespace(click=lambda *args: clicks.append(args))
    times = iter([0, 10])
    monkeypatch.setattr(navigation.time, 'monotonic', lambda: next(times))
    with pytest.raises(EntryUnavailable):
        navigator.to_holowork()
    assert clicks == [(260, 45)]
