"""Independent, one-round multiplayer Auto watchdog and input ownership."""
from dataclasses import fields
import time

from .auto_deadline import AutoDeadline, CountdownObservation, finite_number
from .game_process import GameProcess
from .guarded_click import ClickRequest


def fresh(message, now):
    timestamp = message.get('captured_at')
    return finite_number(timestamp) and 0 <= now-timestamp <= 2


def watch(config, channel, *, game_factory=GameProcess,
          lane_factory=None, alive, clock=time.monotonic, pause=time.sleep):
    game = lane = None
    armed = safe = shutting_down = False
    publish = channel.publish
    def diagnostic(state, **details):
        try:
            publish(state, **details)
        except Exception:
            pass  # A full/unwritable log must never prevent emergency stop.

    def close_lane():
        if lane is not None and not lane.cleanup_complete:
            lane.abort()
            if not lane.cleanup_complete:
                raise RuntimeError('Guarded input cleanup was not confirmed')

    def shutdown(reason):
        nonlocal shutting_down
        shutting_down = True
        diagnostic('closing_game', reason=reason)
        try:
            stopped = game.emergency_stop()
        finally:
            close_lane()
        diagnostic('game_closed', reason=reason, terminated=stopped)
        return 2
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
        if lane_factory is not None:
            lane = lane_factory(config)
            lane.prepare()
        ready_at = clock()
        if not finite_number(ready_at):
            raise ValueError('Invalid watchdog clock')
        publish('ready', game_pid=game.pid, game_image=game.image, ready_at=ready_at,
                input_protocol=1 if lane is not None else None)
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

        observation_fields = {field.name for field in fields(CountdownObservation)}
        last_command = None
        last_sequence = 0
        input_ack = None
        while True:
            message = channel.message('observation')
            observed = None
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
            if lane is not None:
                if not game.backend.is_running(game.handle):
                    return shutdown('game_exited_before_auto')
                if lane.error or lane.process.poll() is not None:
                    raise RuntimeError(lane.error or 'Guarded input worker exited')
                ack = lane.ack
                if (ack is not None and last_command is not None
                        and type(ack.get('sequence')) is int
                        and ack.get('sequence') == last_sequence
                        and ack.get('captured_at') == last_command['request']['captured_at']
                        and ack.get('released') is True):
                    input_ack = last_sequence
            if message:
                values = message.get('observation')
                if isinstance(values, dict) and set(values) <= observation_fields:
                    if lane is not None and values.get('start_authorized') is True:
                        # A fresh positive Auto observation cannot release this
                        # watcher without this round's completed start click.
                        if (last_command is None or last_command['purpose'] != 'start'
                                or type(message.get('input_sequence')) is not int
                                or message['input_sequence'] != last_sequence
                                or input_ack != last_sequence
                                or values.get('captured_at') != last_command['request']['captured_at']):
                            values = dict(values, start_authorized=False)
                    try:
                        candidate = CountdownObservation(**values)
                        if policy.observe(candidate, now=clock()):
                            observed = candidate
                    except (TypeError, ValueError):
                        pass  # Malformed evidence cannot move the deadline.
            if policy.auto_verified:
                close_lane()
                safe = True
                publish('auto_verified', deadline=policy.deadline, input_ack=input_ack)
                return 0
            # A short ready timer may have made the deadline immediate.
            if policy.expired(clock()):
                return shutdown('auto_verification_deadline')
            command = channel.message('click') if lane is not None else None
            if command is not None and command != last_command:
                purpose = command.get('purpose')
                if (purpose not in ('match', 'prepare', 'start')
                        or (purpose == 'match') != (last_sequence == 0)
                        or (last_command is not None and last_command['purpose'] == 'start')
                        or (last_sequence and input_ack != last_sequence)):
                    raise RuntimeError('Guarded click is not valid in this round phase')
                request = ClickRequest.parse(command.get('request'), job,
                                             last_sequence=last_sequence, now=clock())
                request.check_live_purpose(purpose)
                if purpose == 'start' and (observed is None or observed.scene != 'multi_ready'
                        or observed.auto_enabled is not True or type(observed.auto_remaining) is not int
                        or observed.auto_remaining <= 0 or observed.captured_at != request.captured_at):
                    raise RuntimeError('Guarded start click lacks fresh Auto evidence')
                if request.captured_at + AutoDeadline.FRESHNESS >= policy.deadline:
                    return shutdown('guarded_input_exceeds_deadline')
                last_sequence = lane.dispatch(command['request'])
                last_command = command
                input_ack = None
            # There is deliberately no post-arm "home" disarm. A fresh home
            # frame can precede the matching click and race with cancellation.
            # Once armed, only final actual Auto authorization can release it.
            if clock()-heartbeat_at >= .25:
                publish('armed', deadline=policy.deadline, ready_seen=policy.ready_seen,
                        last_capture=policy.last_capture, input_ack=input_ack)
                heartbeat_at = clock()
            pause(.05)
    except BaseException as error:
        detail = f'{type(error).__name__}: {error}'
        if armed and not safe and not shutting_down and game is not None:
            try:
                shutdown('watchdog_error')
            except Exception as shutdown_error:
                detail += f'; emergency_shutdown_failed: {shutdown_error}'
        diagnostic('failed', error=detail)
        return 1
    finally:
        try:
            if not armed:
                close_lane()
        finally:
            if game is not None:
                game.close()
