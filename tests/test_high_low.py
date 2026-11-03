from types import SimpleNamespace

import pytest

from daily.high_low_worker import play


@pytest.mark.parametrize('target,opening,amount,reason', [
    (100, 500, 400, 'target'), (None, 19_800, 400, 'cap'),
])
def test_high_low_goal_waits_for_new_settlement_before_stopping(target, opening, amount, reason):
    events = []
    bot = SimpleNamespace(safe_click=lambda *args: events.append('click'))

    def upstream(*, mode, notify):
        assert mode == 'legacy'
        notify('stats', {'coins': opening})
        assert bot.bot_running
        bot.safe_click(1, 2)
        notify('stats', {'coins': opening + amount})
        assert bot.bot_running
        notify('credit', {'amount': amount})
        assert not bot.bot_running

    bot.auto_play_loop = upstream
    result = play(bot, target, cancelled=lambda: False,
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
        play(bot, 100, cancelled=lambda: False, publish=lambda *a, **k: None,
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
        play(bot, 100, cancelled=lambda: False, publish=lambda *a, **k: None,
             verify_window=lost_window)
    assert not bot.bot_running and bot.safe_click is original
