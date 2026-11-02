from dataclasses import asdict
from types import SimpleNamespace

import pytest

from daily.guarded_click import ClickRequest, FrameCoordinates, GuardedClickError, execute_click
from daily.input_release import ManagedInputRelease
from daily.inputs import Inputs


def message(**overrides):
    return dict(job_id='round-one', sequence=1, x=100, y=200, captured_at=100.0, **overrides)


def test_only_fresh_in_order_click_coordinates_are_accepted():
    value = message()
    request = ClickRequest.parse(value, 'round-one', last_sequence=0, now=101)
    assert asdict(request) == value


@pytest.mark.parametrize('field,value', [('job_id', 'old-round'), ('sequence', True),
    ('sequence', 0), ('sequence', 2), ('x', True), ('x', -1), ('x', 1280),
    ('y', 720), ('y', -1), ('captured_at', True), ('captured_at', 102),
    ('captured_at', 98), ('captured_at', float('nan')), ('captured_at', float('inf'))])
def test_invalid_or_stale_requests_cannot_create_input(field, value):
    packet = message()
    packet[field] = value
    with pytest.raises(GuardedClickError):
        ClickRequest.parse(packet, 'round-one', last_sequence=0, now=101)


def test_protocol_cannot_request_arbitrary_keys_or_actions():
    packet = message()
    packet['key'] = 0x41
    with pytest.raises(GuardedClickError, match='fields'):
        ClickRequest.parse(packet, 'round-one', last_sequence=0, now=101)


class Controller:
    def __init__(self): self.events = []
    def post_key_down(self, key): return self.post(('down', key))
    def post_key_up(self, key): return self.post(('up', key))
    def post_relative_move(self, x, y): return self.post(('move', x, y))
    def post_click(self, x, y): return self.post(('click', x, y))
    def post(self, event):
        self.events.append(event)
        return SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))


def test_local_click_restores_guard_and_releases_owned_alt():
    controller = Controller()
    original = lambda: None
    inputs = Inputs(controller, guard=original)
    request = ClickRequest.parse(message(), 'round-one', last_sequence=0, now=100)
    execute_click(request, inputs, clock=lambda: 100, check_target=lambda: None)
    assert controller.events == [('down', 0xA5), ('move', 1, 0), ('click', 100, 200), ('up', 0xA5)]
    assert inputs.guard is original and not inputs.held


def test_expired_evidence_after_cursor_audit_releases_without_clicking():
    now = [100.0]
    def trace(name, **details):
        if name == 'click': now[0] = 103
    controller = Controller()
    inputs = Inputs(controller, trace=trace)
    request = ClickRequest.parse(message(), 'round-one', last_sequence=0, now=100)
    with pytest.raises(GuardedClickError, match='expired'):
        execute_click(request, inputs, clock=lambda: now[0], check_target=lambda: None)
    assert not any(event[0] == 'click' for event in controller.events)
    assert controller.events[-1] == ('up', 0xA5) and not inputs.held


class Function:
    def __init__(self, action): self.action = action
    def __call__(self, *args): return self.action(*args)


def release_backend():
    events = []
    def send(count, values, size):
        events.append([(value.type, value.ki.wVk, value.ki.dwFlags) if value.type == 1
                       else (value.type, value.mi.dwFlags) for value in values])
        return count
    user = SimpleNamespace(SendInput=Function(send),
        MapVirtualKeyW=Function(lambda key, mode: 0x38),
        GetAsyncKeyState=Function(lambda key: 0))
    return user, events


def test_cleanup_never_sends_before_owned_worker_has_exited():
    user, events = release_backend()
    release = ManagedInputRelease(user=user, check_desktop=lambda: None)
    with pytest.raises(GuardedClickError, match='still post'):
        release.release_after_exit(SimpleNamespace(poll=lambda: None), exclusive_input=True)
    assert not events


def test_emergency_cleanup_only_sends_managed_up_events_without_focus_or_movement():
    user, events = release_backend()
    release = ManagedInputRelease(user=user, check_desktop=lambda: None)
    assert release.released() and not events
    release.release_after_exit(SimpleNamespace(poll=lambda: 1), exclusive_input=True)
    assert events == [[(1, 0xA5, 0x0002), (0, 0x0004)]]


def test_input_reservation_is_required_even_for_an_exited_process():
    user, events = release_backend()
    release = ManagedInputRelease(user=user, check_desktop=lambda: None)
    with pytest.raises(GuardedClickError, match='reserved'):
        release.release_after_exit(SimpleNamespace(poll=lambda: 1))
    assert not events


def test_unreadable_input_desktop_is_not_a_released_key_state():
    user, events = release_backend()
    def inaccessible():
        raise GuardedClickError('desktop unavailable')
    release = ManagedInputRelease(user=user, check_desktop=inaccessible)
    with pytest.raises(GuardedClickError, match='desktop'):
        release.released()
    with pytest.raises(GuardedClickError, match='desktop'):
        release.release_after_exit(SimpleNamespace(poll=lambda: 1), exclusive_input=True)
    assert not events


def test_partial_send_or_still_held_keys_do_not_claim_cleanup_success():
    user, _ = release_backend()
    release = ManagedInputRelease(user=user, check_desktop=lambda: None)
    user.SendInput.action = lambda *args: 1
    with pytest.raises(GuardedClickError, match='fully accepted'):
        release.release_after_exit(SimpleNamespace(poll=lambda: 1), exclusive_input=True)
    user.SendInput.action = lambda *args: 2
    user.GetAsyncKeyState.action = lambda key: 0x8000
    with pytest.raises(GuardedClickError, match='not been confirmed'):
        release.release_after_exit(SimpleNamespace(poll=lambda: 1), exclusive_input=True)


@pytest.mark.parametrize('shape,expected', [((720, 1280, 3), (100, 200)),
                                         ((1080, 1920, 3), (150, 300)),
                                         ((720, 1278, 3), (100, 200))])
def test_disposable_controller_maps_normalized_coordinates_using_its_own_capture(shape, expected):
    controller = Controller()
    adapter = FrameCoordinates(controller)
    adapter.update_frame(shape)
    adapter.post_click(100, 200)
    assert controller.events == [('click', *expected)]


def test_local_controller_rejects_unknown_or_non_game_geometry():
    controller = Controller()
    adapter = FrameCoordinates(controller)
    with pytest.raises(GuardedClickError, match='capture'):
        adapter.post_click(100, 200)
    for shape in [(), (0, 1280, 3), (720, 0, 3), (720, 720, 3)]:
        with pytest.raises(GuardedClickError): adapter.update_frame(shape)
    assert not controller.events
