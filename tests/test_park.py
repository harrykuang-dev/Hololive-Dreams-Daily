from datetime import datetime
import json
from types import SimpleNamespace

import numpy as np
import pytest

from daily import park
from daily.calendar import DailyAttempts, DailyUnavailable
from daily.contracts import TaskConfig, TaskStatus
from daily.navigation import NavigationError, Navigator
from daily.park import GIFT_PLACES, LOTTERY_PLACE, ParkRunner, ParkView


@pytest.mark.parametrize('speaker,lines,lottery,kind', [
    ([], ['這是我的一點小心意，收下吧！'], False, 'dialogue'),
    ([], ['這是我的一點小心意，收下吧！'], True, 'unknown'),
    ([], ['其他還未完成的對話'], False, 'unknown'),
    (['一', '其他'], ['這是我的一點小心意，收下吧！'], False, 'unknown'),
    ([], ['這是我的一點小心意，收下吧！']*2, False, 'unknown'),
    (['正常角色'], ['一般對話'], False, 'dialogue')])
def test_symbol_name_gift_requires_complete_transfer_line_and_dialogue_control(speaker, lines, lottery, kind):
    def text(observation, expected, roi):
        words = lines if roi == (200, 610, 850, 60) else speaker if roi == (550, 560, 170, 50) else []
        return [SimpleNamespace(text=word) for word in words]
    nav = SimpleNamespace(text=text, vision=SimpleNamespace(match=lambda image, name: name == 'park_dialogue_next'))
    view = ParkRunner(nav).read(SimpleNamespace(image=None, scene='home'), LOTTERY_PLACE if lottery else GIFT_PLACES[0])
    assert view.kind == kind


def test_dialogue_footer_recognizes_post_reward_line_without_template():
    def text(observation, expected, roi):
        if roi == (200, 610, 850, 60):
            return [SimpleNamespace(text='BAU BAU——！哈啊～好想玩個痛快～！')]
        if roi == (550, 560, 170, 50):
            return [SimpleNamespace(text='Mococo')]
        return []
    nav = SimpleNamespace(text=text, vision=SimpleNamespace(match=lambda *args: None))
    view = ParkRunner(nav).read(SimpleNamespace(image=None, scene='park_dialogue'), GIFT_PLACES[1])
    assert view.kind == 'dialogue'


def runner_for(tmp_path, monkeypatch, kinds):
    elapsed, events = [0.0], []
    monkeypatch.setattr(park.time, 'monotonic', lambda: elapsed[0])
    def pause(seconds):
        elapsed[0] += seconds
    def key(*args, before_post=None):
        if before_post is not None:
            before_post()
        events.append(('key', args))
    inputs = SimpleNamespace(key=key,
                             click=lambda *args: events.append(('click', args)), pause=pause)
    observation = SimpleNamespace(scene='home')
    nav = SimpleNamespace(inputs=inputs, observe=lambda: observation, gift_transfer_dialogue=lambda _: None,
                          save=lambda *args: 'reward.png')
    nav.daily_attempts = DailyAttempts(tmp_path, clock=lambda: datetime.fromisoformat('2026-11-01T05:00:00+08:00'))
    runner = ParkRunner(nav)
    sequence = iter(kinds)
    last = [None]
    def read(*args):
        value = next(sequence, last[0])
        last[0] = value
        if isinstance(value, Exception):
            raise value
        kind, signature = value if isinstance(value, tuple) else (value, '')
        return ParkView(kind, observation, (590, 620, 90, 30), signature)
    runner.read = read
    return runner, events


def test_failed_attempt_never_repeats_its_first_e_after_restart(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry', RuntimeError('lost capture')])
    with pytest.raises(RuntimeError, match='lost capture'):
        runner.consume(GIFT_PLACES[0])
    assert events == [('key', (0x45, .12))]
    runner.read = lambda *args: ParkView('entry', None)
    with pytest.raises(DailyUnavailable):
        runner.consume(GIFT_PLACES[0])
    assert len(events) == 1


def test_recording_failure_blocks_reservation_and_first_e(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry'])
    def fail():
        raise RuntimeError('recording stopped')
    runner.nav.daily_attempts.guard = fail
    with pytest.raises(RuntimeError, match='recording stopped'):
        runner.consume(LOTTERY_PLACE)
    assert events == []
    assert not list(tmp_path.rglob('*.json'))


def test_lottery_scratch_is_clicked_once_and_get_precedes_success(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch,
        ['entry', 'scratch', 'scratch', 'scratch', 'reward', 'entry', 'entry', 'entry', 'entry'])
    assert runner.consume(LOTTERY_PLACE) == 'reward.png'
    assert events == [('key', (0x45, .12)), ('click', (635, 635)), ('click', (635, 635))]
    record = next(tmp_path.rglob('lottery.json')).read_text()
    assert '"state": "complete"' in record


def test_transient_entry_animation_is_rechecked_before_reserving_or_pressing_e(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch,
        ['prompt_loading', 'park', 'entry', 'scratch', 'reward',
         'entry', 'entry', 'entry', 'entry'])
    assert runner.consume(LOTTERY_PLACE) == 'reward.png'
    assert events[0] == ('key', (0x45, .12))
    assert sum(event[0] == 'key' for event in events) == 1


def test_unchanged_dialogue_has_one_e_and_then_stops(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry', ('dialogue', 'same line')])
    with pytest.raises(NavigationError, match='GET'):
        runner.consume(GIFT_PLACES[1])
    assert events == [('key', (0x45, .12)), ('key', (0x45, .12))]
    assert '"state": "failed"' in next(tmp_path.rglob('*.json')).read_text(encoding='utf-8')


def test_return_without_get_does_not_claim_completion(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry', 'park'])
    with pytest.raises(NavigationError, match='GET'):
        runner.consume(GIFT_PLACES[2])
    assert len(events) == 1


def test_received_gift_can_return_to_character_name_prompt_without_another_e(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch,
        ['entry', 'reward', 'prompt_loading', 'prompt_loading', 'prompt_loading',
         'prompt_loading', 'prompt_loading'])
    assert runner.consume(GIFT_PLACES[0]) == 'reward.png'
    assert events == [('key', (0x45, .12)), ('click', (635, 635))]


def test_entry_only_never_reserves_or_interacts(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.entry = lambda *args, **kwargs: (LOTTERY_PLACE, ParkView('entry', None))
    result = runner.run(TaskConfig.parse({'task': 'lottery', 'entry_only': True}))
    assert result.status == TaskStatus.ENTRY_VERIFIED
    assert events == []
    assert not list(tmp_path.rglob('*.json'))


def test_pending_progress_starts_only_after_attempt_reservation(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['entry'])
    def before_input():
        assert next(tmp_path.rglob('lottery.json')).exists()
        assert not events
        raise RuntimeError('cancelled before E')
    with pytest.raises(RuntimeError, match='cancelled'):
        runner.consume(LOTTERY_PLACE, before_input)
    assert events == []


def test_known_open_lottery_dialogue_resumes_without_entering_or_pressing_initial_e(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.read = lambda *args: ParkView('dialogue', None, signature='究竟會刮出什麼呢～要來玩一張看看嗎')
    finished = []
    runner.nav.daily_attempts.resume_open_dialogue = lambda task: SimpleNamespace(
        finish=lambda **kw: finished.append(kw))
    runner.finish_interaction = lambda place: 'received.png'
    runner.entry = lambda *args, **kw: pytest.fail('Must not reenter daily interaction')
    result = runner.run(TaskConfig.parse({'task': 'lottery', 'entry_only': False}))
    assert result.status == TaskStatus.COMPLETE and result.completed_rounds == 1
    assert events == [] and finished == [{'evidence': 'received.png'}]


def test_failed_gift_location_is_excluded_while_other_locations_remain_independent(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.nav.daily_attempts.reserve(GIFT_PLACES[0].key).finish(error='uncertain')
    consumed = []
    def entry(task, *, exclude):
        assert GIFT_PLACES[0].key in exclude
        if GIFT_PLACES[1].key not in exclude:
            return GIFT_PLACES[1], ParkView('entry', None)
        raise park.EntryUnavailable('No remaining notifications')
    runner.entry = entry
    runner.consume = lambda place, before_input: consumed.append(place.key) or 'received.png'
    result = runner.run(TaskConfig.parse({'task': 'park_gifts', 'entry_only': False}))
    assert result.completed_rounds == 1 and result.status == TaskStatus.COMPLETE
    assert consumed == [GIFT_PLACES[1].key] and events == []


@pytest.mark.parametrize('change', [None, 'window', 'nameplate', 'second_e'])
def test_house_handover_resumes_once_only_from_same_recorded_interaction(tmp_path, monkeypatch, change):
    runner, events = runner_for(tmp_path, monkeypatch, ['reward', 'park'])
    video = tmp_path / 'previous.avi'
    video.touch()
    timeline = tmp_path / 'events.jsonl'
    ledger = runner.nav.daily_attempts
    ledger.recording = lambda: {'video': str(video), 'timeline': str(timeline)}
    original = ledger.reserve(GIFT_PLACES[0].key)
    original.finish(error='unreadable speaker')
    press = {'event': 'key_down', 'key': 0x45, 'utc': original.record['started_at']}
    release = {'event': 'key_up', 'key': 0x45}
    history = [{'event': 'recording_ready'}, press, release]
    if change == 'second_e':
        history += [press, release]
    history.append({'event': 'recording_stopped', 'frames': 5})
    timeline.write_text('\n'.join(map(json.dumps, history)), encoding='utf-8')
    reference = np.zeros((720, 1280, 3), dtype=np.uint8)
    current = reference.copy()
    if change == 'nameplate':
        current[573:594, 612:672] = 100
    monkeypatch.setattr(park.cv2, 'VideoCapture', lambda _: SimpleNamespace(
        set=lambda *_: True, read=lambda: (True, reference), get=lambda _: 5, release=lambda: None))
    runner.nav.vision = SimpleNamespace(normalize=lambda frame: frame)
    runner.nav.recording = SimpleNamespace(matches_window=lambda _: change != 'window')
    runner.nav.observe = lambda: SimpleNamespace(scene='home', image=current, captured_at=park.time.monotonic())
    runner.nav.gift_transfer_dialogue = lambda _: '這是我的一點小心意，收下吧！'
    runner.entry = lambda *_a, **_kw: pytest.fail('Must not initiate a new gift interaction')
    config = TaskConfig.parse({'task': 'park_gifts', 'entry_only': False})
    if change:
        with pytest.raises(DailyUnavailable):
            runner.run(config)
        assert not events and not original.path.with_suffix('.recovery').exists()
    else:
        assert runner.run(config).completed_rounds == 1
        assert events == [('key', (0x45, .12)), ('click', (635, 635))]
        with pytest.raises(DailyUnavailable):
            runner.run(config)
        assert len(events) == 2


@pytest.mark.parametrize('count', [0, 2])
def test_ambiguous_floating_gift_markers_stop_before_camera_or_walking_input(tmp_path, monkeypatch, count):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.nav.vision = SimpleNamespace(find_all=lambda *a, **k: [SimpleNamespace(box=(900, 250, 37, 35))]*count)
    with pytest.raises(NavigationError, match='唯一'):
        runner.house_gift_offset(ParkView('park', SimpleNamespace(image=None)))
    assert not events


def prepare_house_entry(runner):
    runner.nav.open_notifications = lambda: object()
    runner.nav.text = lambda *a, **k: [SimpleNamespace(box=(100, 190, 180, 30))] if a[1] == GIFT_PLACES[0].notice else []
    runner.wait_for_arrival = lambda place: None


@pytest.mark.parametrize('blue', [(254, 194, 38), (254, 151, 46)])
def test_park_loading_requires_both_borders_and_blue_pixels(blue):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:75] = (251, 248, 228)
    frame[645:] = (251, 248, 228)
    assert not Navigator.park_teleport_loading(frame)
    frame[645:, :900] = blue
    assert Navigator.park_teleport_loading(frame)
    frame[:75, :640] = 0
    assert not Navigator.park_teleport_loading(frame)


def test_harmony_garden_loading_requires_green_field_and_white_logo():
    frame = np.full((720, 1280, 3), (107, 122, 52), dtype=np.uint8)
    assert not Navigator.park_teleport_loading(frame)
    frame[205:365, 710:840] = 255
    assert not Navigator.park_teleport_loading(frame)
    frame[:, 1080:] = (169, 189, 234)
    assert Navigator.park_teleport_loading(frame)
    frame[:, :400] = 0
    assert not Navigator.park_teleport_loading(frame)


def test_park_arrival_ignores_old_home_and_requires_stable_home_after_loading(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    scenes = ['home'] * 4 + ['park_teleport_loading'] + ['home', None] + ['home'] * 3
    captured = []

    def observe(*, park_loading_only):
        assert park_loading_only == ('park_teleport_loading' not in captured)
        scene = scenes[len(captured)]
        captured.append(scene)
        return SimpleNamespace(scene=scene, captured_at=park.time.monotonic())

    runner.nav.observe = observe
    runner.wait_for_arrival(GIFT_PLACES[1])
    assert captured == scenes
    assert not events


def test_park_arrival_never_accepts_home_without_loading(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, [])
    runner.nav.observe = lambda **kwargs: SimpleNamespace(scene='home', captured_at=park.time.monotonic())
    with pytest.raises(NavigationError, match='未確認樂園傳送完成'):
        runner.wait_for_arrival(GIFT_PLACES[1])
    assert not events


def test_gift_camera_stops_if_fresh_marker_moves_further_from_center(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['park'])
    prepare_house_entry(runner)
    offsets = iter((200, 250))
    runner.house_gift_offset = lambda view: next(offsets)
    runner.nav.inputs.look = lambda dx: events.append(('camera', dx))
    with pytest.raises(NavigationError, match='接近中央'):
        runner.entry('park_gifts')
    assert events == [('click', (190, 205)), ('camera', 30)]


def test_gift_camera_calibrates_recorded_response_within_existing_move_limit(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['park'])
    prepare_house_entry(runner)
    # Native recording: initial offset182.5, dx27 moved marker35 pixels.
    offset = [182.5]
    runner.house_gift_offset = lambda view: offset[0]
    observation = SimpleNamespace(image=None)
    runner.read = lambda *args: ParkView('entry' if abs(offset[0]) <= 35 else 'park', observation)
    def move(dx):
        events.append(('camera', dx))
        offset[0] -= dx*35/27
    runner.nav.inputs.look = move
    place, view = runner.entry('park_gifts')
    assert place == GIFT_PLACES[0] and view.kind == 'entry'
    moves = [value for kind, value in events if kind == 'camera']
    assert 1 < len(moves) <= 6 and all(abs(dx) <= 40 for dx in moves)
    assert not any(kind == 'key' for kind, _ in events)


def test_unresponsive_camera_retains_twelve_move_stop_and_evidence(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['park'])
    prepare_house_entry(runner)
    runner.house_gift_offset = lambda view: 182.5
    runner.nav.inputs.look = lambda dx: events.append(('camera', dx))
    saved = []
    runner.nav.save = lambda observation, label: saved.append(label)
    with pytest.raises(NavigationError, match='上限'):
        runner.entry('park_gifts')
    assert len([event for event in events if event[0] == 'camera']) == 12
    assert saved == ['gift-camera-limit']
    assert not any(kind == 'key' for kind, _ in events)


def test_prompt_appearing_while_waiting_for_marker_prevents_further_movement(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['park', 'entry'])
    prepare_house_entry(runner)
    def missing(view):
        raise park.GiftMarkerMissing('temporary animation')
    runner.house_gift_offset = missing
    place, view = runner.entry('park_gifts')
    assert place == GIFT_PLACES[0] and view.kind == 'entry'
    assert events == [('click', (190, 205))]


def test_missing_gift_marker_wait_is_bounded_without_camera_or_walking(tmp_path, monkeypatch):
    runner, events = runner_for(tmp_path, monkeypatch, ['park'])
    prepare_house_entry(runner)
    def missing(view):
        raise park.GiftMarkerMissing('missing')
    runner.house_gift_offset = missing
    with pytest.raises(park.GiftMarkerMissing):
        runner.entry('park_gifts')
    assert events == [('click', (190, 205))]
