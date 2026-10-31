from types import SimpleNamespace

import pytest

from daily.inputs import Cancelled, InputFailure, Inputs


class Job:
    def wait(self):
        return SimpleNamespace(succeeded=True)


class Controller:
    def __init__(self):
        self.events = []
        self.fail_click = False

    def post_key_down(self, key):
        self.events.append(('down', key))
        return Job()

    def post_key_up(self, key):
        self.events.append(('up', key))
        return Job()

    def post_relative_move(self, x, y):
        self.events.append(('move', x, y))
        return Job()

    def post_click(self, x, y):
        self.events.append(('click', x, y))
        if self.fail_click:
            raise InputFailure('failed')
        return Job()


def test_right_alt_move_click_release_order():
    controller = Controller()
    inputs = Inputs(controller)
    inputs.click(10, 20)
    assert controller.events == [('down', 0xA5), ('move', 1, 0), ('click', 10, 20), ('up', 0xA5)]
    assert not inputs.held


def test_click_failure_still_releases_alt():
    controller = Controller()
    controller.fail_click = True
    inputs = Inputs(controller)
    with pytest.raises(InputFailure):
        inputs.click(10, 20)
    assert controller.events[-1] == ('up', 0xA5)
    assert not inputs.held


def test_cancellation_does_not_click():
    controller = Controller()
    inputs = Inputs(controller, stopping=lambda: True)
    with pytest.raises(Cancelled):
        inputs.click(10, 20)
    assert not controller.events
