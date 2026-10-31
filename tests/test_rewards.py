from types import SimpleNamespace

import pytest

from daily import rewards
from daily.rewards import RewardView, RewardsRunner
from daily.navigation import NavigationError


def make_runner(monkeypatch, views):
    clicks, progress = [], []
    nav = SimpleNamespace(
        inputs=SimpleNamespace(click=lambda *xy: clicks.append(xy), pause=lambda _: None),
        save=lambda *args: 'evidence.png',
        text=lambda *args: [SimpleNamespace(box=(600, 650, 100, 30))])
    runner = RewardsRunner(nav, progress.append)
    sequence = iter(views)
    runner.read = lambda *args: next(sequence, views[-1])
    ticks = iter(i*.2 for i in range(2000))
    monkeypatch.setattr(rewards.time, 'monotonic', lambda: next(ticks))
    return runner, clicks, progress


def view(page='daily', state='claimable'):
    return RewardView(object(), page, state, (1000, 620, 150, 30))


def test_disabled_button_never_clicked(monkeypatch):
    runner, clicks, progress = make_runner(monkeypatch, [view(state='empty')])
    runner.collect('daily')
    assert not clicks and not progress and runner.claimed == 0


def test_claim_not_repeated_without_positive_confirmation(monkeypatch):
    runner, clicks, progress = make_runner(monkeypatch, [view()])
    with pytest.raises(NavigationError, match='未再次點擊'):
        runner.collect('daily')
    assert clicks == [(1075, 635)]
    assert progress[-1].pending_round and runner.claimed == 0


def test_wrong_page_with_shared_header_cannot_authorize_claim(monkeypatch):
    runner, clicks, _ = make_runner(monkeypatch, [view(page='pass')])
    with pytest.raises(NavigationError, match='無法確認'):
        runner.collect('daily')
    assert not clicks


def test_reward_popup_is_closed_only_once(monkeypatch):
    runner, clicks, progress = make_runner(monkeypatch, [view()]*4 + [view(None, 'get')]*4
                                          + [view(state='empty')]*8)
    runner.collect('daily')
    assert clicks == [(1075, 635), (650, 665)]
    assert runner.claimed == 1 and not progress[-1].pending_round


def test_direct_mission_points_confirmation_without_get_popup(monkeypatch):
    runner, clicks, _ = make_runner(monkeypatch, [view()]*4 + [view(state='empty')]*8)
    runner.collect('daily')
    assert clicks == [(1075, 635)] and runner.claimed == 1
