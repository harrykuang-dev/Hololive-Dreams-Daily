from types import SimpleNamespace

import pytest

from daily.high_low_worker import play
from daily.high_low import HighLowRunner
from daily.calendar import DailyUnavailable
from daily.contracts import TaskConfig, TaskStatus
from daily.navigation import NavigationError


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


@pytest.mark.parametrize('start_from_prompt', [False, True])
@pytest.mark.parametrize('table_opens', [False, True])
def test_high_low_waits_and_clicks_only_once_without_reading_dialogue(
        monkeypatch, start_from_prompt, table_opens):
    clock = [0.]
    monkeypatch.setattr('daily.high_low.time.monotonic', lambda: clock[0])
    entry = SimpleNamespace(scene='home', image='entry')
    foreign = SimpleNamespace(scene='park_dialogue', image='gift')
    dialogue = SimpleNamespace(scene='park_dialogue', image='high_low')
    table = SimpleNamespace(scene=None, image='table')
    events = []
    observations = iter([entry, foreign, dialogue] if start_from_prompt else [dialogue, dialogue])

    def observe():
        return next(observations, table if table_opens and clock[0] >= 3 else dialogue)

    def text(observation, pattern, roi):
        assert observation is entry and pattern == '^比大小$'
        return [object()]

    def pause(seconds):
        clock[0] += seconds

    nav = SimpleNamespace(observe=observe, text=text,
        vision=SimpleNamespace(
            match=lambda image, name: SimpleNamespace(box=(980, 674, 74, 32)),
            find_all=lambda image, name, **kwargs: [object()] if image == 'high_low' else []),
        inputs=SimpleNamespace(key=lambda key, duration: events.append(('key', key, clock[0])),
                               click=lambda *xy: events.append(('click', xy, clock[0])),
                               pause=pause),
        save=lambda *args: None)
    runner = HighLowRunner(nav, None, None)
    runner.table = lambda observation: observation is table
    if table_opens:
        runner.open_table()
    else:
        with pytest.raises(NavigationError, match='未確認比大小牌桌'):
            runner.open_table()
    assert events == ([('key', 0x45, 0), ('click', (1039, 690), 2.7)] if start_from_prompt
                      else [('click', (1039, 690), 2.5)])


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
