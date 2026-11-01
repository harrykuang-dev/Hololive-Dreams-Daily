from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from daily import navigation
from daily.navigation import EntryUnavailable, NavigationError, Navigator, Observation


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
        return SimpleNamespace(hit=True, filtered_results=[SimpleNamespace(text=w, box=(1, 2, 3, 4)) for w in self.words])


@pytest.mark.parametrize('labels,icon,accepted', [(0, True, True), (0, False, False),
                                                (1, False, True), (2, True, False)])
def test_games_navigation_requires_home_and_positive_label_or_gamepad(labels, icon, accepted):
    nav = Navigator.__new__(Navigator)
    calls = []
    nav.to_home = lambda: calls.append('home')
    nav.wait_for = lambda scenes: Observation(None, next(iter(scenes)), 0)
    nav.text = lambda *a, **k: [SimpleNamespace(box=(814,37,29,24))] * labels
    nav.vision = SimpleNamespace(match=lambda *a: SimpleNamespace(box=(814,37,29,24)) if icon else None)
    nav.inputs = SimpleNamespace(click=lambda *a: calls.append(a))
    if accepted:
        assert nav.to_minigame_portal().scene == 'minigame_portal'
        assert calls == ['home', (828,49)]
    else:
        with pytest.raises(NavigationError): nav.to_minigame_portal()
        assert calls == ['home']


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


@pytest.mark.parametrize('labels,button_count,accepted', [
    (['日期已變更'], 1, True), (['日期已變更'], 2, False),
    (['其他確認'], 1, False), (['日期已變更'], 0, False)])
def test_date_recovery_requires_exact_notice_and_unique_ok(labels, button_count, accepted):
    navigator = Navigator.__new__(Navigator)
    def text(observation, expected, roi):
        words = labels if expected == '^日期已變更$' else ['OK'] * button_count
        return [SimpleNamespace(text=word, box=(1100, 649, 33, 16)) for word in words]
    navigator.text = text
    assert (navigator.date_change_button(None) is not None) == accepted


@pytest.mark.parametrize('scene', ['date_changed', 'login_bonus', 'login_bonus_standard', 'holowork_get', 'login_notice', 'game_title', 'game_title_ready'])
def test_unchanged_recovery_notice_is_acknowledged_once_then_times_out(monkeypatch, scene):
    navigator = Navigator.__new__(Navigator)
    now, clicks = [0.], []
    def tick():
        now[0] += 1
        return now[0]
    monkeypatch.setattr(navigation.time, 'monotonic', tick)
    monkeypatch.setattr(navigation.time, 'sleep', lambda seconds: None)
    navigator.observe = lambda: Observation(None, scene, 0)
    navigator.date_change_button = lambda observation: (1100, 649, 33, 16)
    navigator.login_bonus_button = navigator.date_change_button
    navigator.received_reward_button = navigator.date_change_button
    navigator.standard_login_bonus_button = navigator.date_change_button
    navigator.login_notice_button = navigator.date_change_button
    navigator.game_title_button = navigator.date_change_button
    navigator.game_title_ready_button = navigator.date_change_button
    navigator.inputs = SimpleNamespace(click=lambda *args: clicks.append(args))
    navigator.save = lambda *args: 'date-stalled.png'
    with pytest.raises(NavigationError, match='逾時'):
        navigator.wait_for({'home'}, timeout=3)
    assert clicks == [(1116, 657)]


@pytest.mark.parametrize('title,hint,accepted', [
    ('萬聖節', '點擊繼續', True), ('商店', '點擊繼續', False),
    ('萬聖節', '購買', False), ('萬聖節', '', False)])
def test_login_recovery_requires_event_title_and_exact_continue_hint(title, hint, accepted):
    navigator = Navigator.__new__(Navigator)
    navigator.text = lambda observation, expected, roi, **kw: [SimpleNamespace(
        text=title if expected == '^萬聖節$' else hint, box=(560, 625, 180, 50))]
    assert (navigator.login_bonus_button(None) is not None) == accepted


@pytest.mark.parametrize('version,engine,buttons,accepted', [
    ('ver.1.2.101', 'CRIWARE', ['登入'], True),
    ('ver.1.2.101', '', ['登入'], False),
    ('invalid', 'CRIWARE', ['登入'], False),
    ('ver.1.2.101', 'CRIWARE', ['登入', '登入'], False)])
def test_game_entry_needs_version_engine_mark_and_unique_button(version, engine, buttons, accepted):
    navigator = Navigator.__new__(Navigator)
    def text(observation, expected, roi):
        words = [version] if roi[0] == 10 else [engine] if roi[0] == 1000 else buttons
        return [SimpleNamespace(text=word, box=(610, 648, 58, 30)) for word in words]
    navigator.text = text
    assert (navigator.game_title_button(None) is not None) == accepted


def test_game_title_login_and_tap_start_are_distinct_once_only_steps(monkeypatch):
    navigator = Navigator.__new__(Navigator)
    scenes = iter(['game_title', None, 'game_title_ready', 'home', 'home'])
    navigator.observe = lambda: Observation(None, next(scenes), 0)
    navigator.game_title_button = lambda observation: (610, 648, 58, 30)
    navigator.game_title_ready_button = lambda observation: (554, 652, 170, 23)
    clicks = []
    navigator.inputs = SimpleNamespace(click=lambda *args: clicks.append(args))
    monkeypatch.setattr(navigation.time, 'sleep', lambda seconds: None)
    assert navigator.wait_for({'home'}, stable_for=0).scene == 'home'
    assert clicks == [(639, 663), (639, 663)]


def test_reset_login_reward_chain_returns_home_without_repeated_confirmations(monkeypatch):
    navigator = Navigator.__new__(Navigator)
    frames = iter(['date_changed', 'login_bonus', 'holowork_get',
                   'login_bonus_standard', 'holowork_get', 'login_notice', 'home', 'home'])
    navigator.observe = lambda: Observation(None, next(frames), 0)
    navigator.date_change_button = lambda observation: (1100, 649, 33, 16)
    navigator.login_bonus_button = lambda observation: (560, 625, 180, 50)
    navigator.received_reward_button = lambda observation: (540, 635, 200, 40)
    navigator.standard_login_bonus_button = lambda observation: (800, 580, 200, 55)
    navigator.login_notice_button = lambda observation: (1060, 85, 60, 60)
    clicks = []
    navigator.inputs = SimpleNamespace(click=lambda *args: clicks.append(args))
    monkeypatch.setattr(navigation.time, 'sleep', lambda seconds: None)
    assert navigator.wait_for({'home'}, stable_for=0).scene == 'home'
    assert clicks == [(1116, 657), (650, 650), (640, 655), (900, 607), (640, 655), (1090, 115)]


def test_holowork_header_ocr_handles_changing_dispatch_background(tmp_path):
    root = Path(__file__).resolve().parents[1]
    navigator = Navigator(Context(['holowork']), root / 'resource', tmp_path)
    assert navigator.observe().scene == 'holowork'


def test_main_menu_requires_multiple_distinct_menu_labels(tmp_path):
    root = Path(__file__).resolve().parents[1]
    navigator = Navigator(Context(['個人檔案', '會員資格', '持有物', '其他']), root / 'resource', tmp_path)
    assert navigator.observe().scene == 'main_menu'


@pytest.mark.parametrize('scene,expected', [
    ('gifts', [{'main_menu'}, {'home'}]),
    ('multi_portal', [{'live_portal'}, {'home'}]),
    ('event_missions', [{'event_home'}, {'event_list'}, {'home'}])])
def test_nested_reward_pages_return_through_observed_parents(scene, expected):
    navigator = Navigator.__new__(Navigator)
    navigator.observe = lambda: Observation(None, scene, 0)
    navigator.vision = SimpleNamespace(match=lambda *args: None)
    clicks, destinations = [], []
    navigator.inputs = SimpleNamespace(click=lambda *args: clicks.append(args))
    def wait(scenes):
        destinations.append(scenes)
        return Observation(None, next(iter(scenes)), 0)
    navigator.wait_for = wait
    assert navigator.to_home().scene == 'home'
    assert destinations == expected
    assert clicks == [(1223, 55)]*len(expected)


@pytest.mark.parametrize('scene', ['jump_setup', 'cooking_setup'])
@pytest.mark.parametrize('parent', ['home', 'minigame_portal'])
def test_minigame_back_accepts_direct_park_or_portal_without_retry(scene, parent):
    navigator = Navigator.__new__(Navigator)
    navigator.observe = lambda: Observation(None, scene, 0)
    navigator.vision = SimpleNamespace(match=lambda *args: None)
    clicks, destinations = [], []
    navigator.inputs = SimpleNamespace(click=lambda *args: clicks.append(args))
    def wait(scenes):
        destinations.append(scenes)
        actual = parent if len(destinations) == 1 else 'home'
        assert actual in scenes
        return Observation(None, actual, 0)
    navigator.wait_for = wait
    assert navigator.to_home().scene == 'home'
    assert destinations[0] == {'minigame_portal', 'home'}
    assert len(clicks) == (1 if parent == 'home' else 2)


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
