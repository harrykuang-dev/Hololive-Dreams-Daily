"""Session policy around the pinned High & Low gameplay loop."""
import time


DAILY_COIN_CAP = 20_000


def play(bot, target, *, cancelled, publish, verify_window):
    """Stop at a confirmed reward goal or at the game's daily limit."""
    baseline = None
    coins = 0
    credited = wins = 0
    reason = None
    click_error = None
    original_click = bot.safe_click

    def guarded_click(*args):
        nonlocal click_error
        try:
            if cancelled():
                raise InterruptedError('High & Low cancelled')
            verify_window()
            return original_click(*args)
        except Exception as error:
            click_error = error
            bot.bot_running = False
            raise

    def notify(kind, values):
        nonlocal baseline, coins, credited, wins, reason
        if cancelled():
            bot.bot_running = False
            raise InterruptedError('High & Low cancelled')
        if kind == 'daily_reset':
            raise RuntimeError('High & Low game day changed during play')
        if kind == 'stats':
            coins = values.get('coins')
            if type(coins) is not int or coins < 0:
                raise RuntimeError('Invalid High & Low daily accounting')
            if baseline is None:
                baseline = coins
                if coins >= DAILY_COIN_CAP:
                    reason = 'cap'
                    bot.bot_running = False
            elif coins < baseline:
                raise RuntimeError('High & Low coin balance regressed')
        elif kind == 'credit':
            amount = values.get('amount')
            if type(amount) is not int or amount <= 0 or coins != baseline + credited + amount:
                raise RuntimeError('Unverified High & Low settlement')
            credited += amount
            wins += 1
            publish('running', phase='settled', coins=coins, credited=credited, wins=wins)
            if target is not None and credited >= target:
                reason = 'target'
                bot.bot_running = False
            elif coins >= DAILY_COIN_CAP:
                reason = 'cap'
                bot.bot_running = False

    try:
        bot.safe_click = guarded_click
        bot.bot_running = True
        bot.auto_play_loop(mode='legacy', notify=notify)
    finally:
        bot.bot_running = False
        bot.safe_click = original_click
    if click_error is not None:
        raise click_error
    if cancelled():
        raise InterruptedError('High & Low cancelled')
    if reason is None:
        raise RuntimeError('High & Low ended without a verified goal or game limit')
    return {'credited': credited, 'wins': wins, 'reason': reason}


def wait_for_table(bot, cancelled, *, timeout=20):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        if cancelled():
            raise InterruptedError('High & Low cancelled')
        frame, _, _ = bot.capture_game_window()
        state = bot.detect_game_state(frame) if frame is not None else None
        if state == previous == 'START_BET':
            return state
        previous = state
        time.sleep(.2)
    raise RuntimeError('High & Low table did not reach a verified idle state')
