from types import SimpleNamespace
import re

import pytest

from daily.high_low_worker import play
from daily.high_low import HighLowRunner
from daily.calendar import DailyUnavailable
from daily.contracts import TaskConfig, TaskStatus


@pytest.mark.parametrize('strategy,mode,opening,reason', [
    ('conservative', 'legacy', 19_800, 'cap'), ('aggressive', 'phased', 500, 'phases'),
])
def test_high_low_strategy_uses_pinned_completion(strategy, mode, opening, reason):
    events = []
    bot = SimpleNamespace(safe_click=lambda *args: events.append('click'), load_daily_stage=lambda: 3)
    selected = mode
    amount = 400

    def upstream(*, mode, notify):
        assert mode == selected
        notify('stats', {'coins': opening})
        assert bot.bot_running
        bot.safe_click(1, 2)
        notify('stats', {'coins': opening + amount})
        assert bot.bot_running
        notify('credit', {'amount': amount})
        assert bot.bot_running is (strategy == 'aggressive')

    bot.auto_play_loop = upstream
    result = play(bot, strategy, cancelled=lambda: False,
                  publish=lambda *a, **k: events.append('settled'),
                  verify_window=lambda: events.append('window'))
    assert result == {'credited': amount, 'wins': 1, 'reason': reason}
    assert events == ['window', 'click', 'settled']


def test_high_low_never_accepts_unconfirmed_credit():
    def upstream(*, mode, notify):
        notify('stats', {'coins': 100})
        notify('credit', {'amount': 400})

    bot = SimpleNamespace(auto_play_loop=upstream, safe_click=lambda: None)
    with pytest.raises(RuntimeError, match='Unverified'):
        play(bot, 'conservative', cancelled=lambda: False, publish=lambda *a, **k: None,
             verify_window=lambda: None)
    assert not bot.bot_running


def test_high_low_guard_blocks_click_on_window_change_even_if_upstream_swallows_error():
    original = lambda *a: pytest.fail('Input reached the wrong window')
    bot = SimpleNamespace(safe_click=original)

    def upstream(*, mode, notify):
        try:
            bot.safe_click(1, 2)
        except RuntimeError:
            pass

    def lost_window():
        raise RuntimeError('Window changed')

    bot.auto_play_loop = upstream
    with pytest.raises(RuntimeError, match='Window changed'):
        play(bot, 'conservative', cancelled=lambda: False, publish=lambda *a, **k: None,
             verify_window=lost_window)
    assert not bot.bot_running and bot.safe_click is original


def test_high_low_advances_the_already_open_shiori_invitation_once():
    text_by_roi = {(550, 560, 170, 50): 'Shiori',
                   (200, 610, 850, 60): '若想抓住機會，那就全力以赴吧。'}
    dialogue = SimpleNamespace(scene='park_dialogue', image=None)
    table = SimpleNamespace(scene=None, image=None)
    observations = iter((dialogue, dialogue, table))
    events = []

    def text(observation, pattern, roi):
        value = text_by_roi.get(roi, '')
        return [SimpleNamespace(text=value)] if re.search(pattern, value) else []

    nav = SimpleNamespace(observe=lambda: next(observations), text=text,
        inputs=SimpleNamespace(key=lambda key, duration: events.append(key), pause=lambda _: None))
    runner = HighLowRunner(nav, None, None)
    runner.table = lambda observation: observation is table
    runner.open_table()
    assert events == [0x45]


@pytest.mark.parametrize('final_screen', [True, False])
def test_already_attempted_high_low_can_only_close_verified_result(final_screen):
    nav = SimpleNamespace(daily_attempts=SimpleNamespace(attempted_tasks=lambda: {'high_low'}),
                          observe=lambda: object(), save=lambda *args: 'home.png')
    runner = HighLowRunner(nav, None, None)
    runner.finished_button = lambda _: (930, 610, 300, 75) if final_screen else None
    events = []
    runner.leave_table = lambda: events.append('close_only')
    if final_screen:
        result = runner.run(TaskConfig('high_low'))
        assert result.status == TaskStatus.COMPLETE and result.completed_rounds == 0
        assert events == ['close_only']
    else:
        with pytest.raises(DailyUnavailable):
            runner.run(TaskConfig('high_low'))
        assert not events
