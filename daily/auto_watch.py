"""Independent, one-round multiplayer Auto watchdog; no game input APIs."""
from dataclasses import fields
import time

from .auto_deadline import AutoDeadline, CountdownObservation, finite_number
from .game_process import GameProcess


def fresh(message, now):
    timestamp = message.get('captured_at')
    return finite_number(timestamp) and 0 <= now-timestamp <= 2


def watch(config, channel, *, game_factory=GameProcess,
          alive, clock=time.monotonic, pause=time.sleep):
    game = None
    armed = safe = False
    publish = channel.publish
    def diagnostic(state, **details):
        try:
            publish(state, **details)
        except Exception:
            pass  # A full/unwritable log must never prevent emergency stop.
    try:
        job = config['job_id']
        parent = config['parent_pid']
        if (not isinstance(job, str) or not job or type(parent) is not int or parent <= 0
                or type(config['game_pid']) is not int or config['game_pid'] <= 0
                or not isinstance(config['game_image'], str) or not config['game_image']):
            raise ValueError('Invalid watchdog identity')
        # This worker opens and retains its OWN handle. A parent's numeric
        # handle is never passed across processes or assumed to be inherited.
        game = game_factory(config['hwnd'], expected_pid=config['game_pid'],
                            expected_image=config['game_image'])
        ready_at = clock()
        if not finite_number(ready_at):
            raise ValueError('Invalid watchdog clock')
        publish('ready', game_pid=game.pid, game_image=game.image, ready_at=ready_at)
        ready_deadline = ready_at+10
        while True:
            now = clock()
            if not alive(parent):
                publish('cancelled_before_arm')
                return 2
            if channel.flag('cancel_before_arm'):
                publish('cancelled_before_arm')
                return 2
            if channel.error:
                raise RuntimeError(channel.error)
            message = channel.message('arm')
            now = clock()
            if not finite_number(now):
                raise ValueError('Invalid watchdog clock')
            if not alive(parent) or channel.flag('cancel_before_arm'):
                publish('cancelled_before_arm')
                return 2
            if now >= ready_deadline:
                raise TimeoutError('Watchdog was not armed before matchmaking')
            if (message and fresh(message, now) and message['captured_at'] >= ready_at
                    and message.get('decision') == 'arm'):
                policy = AutoDeadline(now)
                armed = True
                publish('armed', deadline=policy.deadline)
                heartbeat_at = now
                break
            pause(.05)

        def shutdown(reason):
            diagnostic('closing_game', reason=reason)
            stopped = game.emergency_stop()
            diagnostic('game_closed', reason=reason, terminated=stopped)
            return 2

        observation_fields = {field.name for field in fields(CountdownObservation)}
        while True:
            message = channel.message('observation')
            now = clock()
            # Cancellation/parent loss and elapsed deadlines win over an Auto
            # proof arriving in the same polling cycle.
            if policy.expired(now):
                return shutdown('auto_verification_deadline')
            if not alive(parent):
                return shutdown('parent_exited_before_auto')
            if channel.flag('abort'):
                return shutdown('requested_abort_before_auto')
            if channel.error:
                raise RuntimeError(channel.error)
            if message:
                values = message.get('observation')
                if isinstance(values, dict) and set(values) <= observation_fields:
                    try:
                        policy.observe(CountdownObservation(**values), now=clock())
                    except (TypeError, ValueError):
                        pass  # Malformed evidence cannot move the deadline.
            if policy.auto_verified:
                safe = True
                publish('auto_verified', deadline=policy.deadline)
                return 0
            # A short ready timer may have made the deadline immediate.
            if policy.expired(clock()):
                return shutdown('auto_verification_deadline')
            # There is deliberately no post-arm "home" disarm. A fresh home
            # frame can precede the matching click and race with cancellation.
            # Once armed, only final actual Auto authorization can release it.
            if clock()-heartbeat_at >= .25:
                publish('armed', deadline=policy.deadline, ready_seen=policy.ready_seen,
                        last_capture=policy.last_capture)
                heartbeat_at = clock()
            pause(.05)
    except BaseException as error:
        detail = f'{type(error).__name__}: {error}'
        if armed and not safe and game is not None:
            try:
                diagnostic('closing_game', reason='watchdog_error', error=detail)
                game.emergency_stop()
            except Exception as shutdown_error:
                detail += f'; emergency_shutdown_failed: {shutdown_error}'
        diagnostic('failed', error=detail)
        return 1
    finally:
        if game is not None:
            game.close()
