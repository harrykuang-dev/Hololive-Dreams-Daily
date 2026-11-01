"""One upstream module per process; no module imports occur during parent startup."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from daily.contracts import TaskConfig
from daily.processes import parent_alive, publish_status
from daily.worker_start import wait_for_start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--status', type=Path, required=True)
    parser.add_argument('--stop', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--module-root', type=Path, required=True)
    args = parser.parse_args()
    config = TaskConfig.parse(json.loads(args.config.read_text(encoding='utf-8')))
    if config.task not in ('fishing', 'jump_rope', 'cooking', 'high_low'):
        raise ValueError('Unsupported module')
    source = args.module_root / config.task
    if not source.is_dir(): raise FileNotFoundError(source)
    sys.path.insert(0, str(source))
    os.chdir(source)
    stopped = threading.Event()
    finished = threading.Event()
    stop_hooks = []
    def publish(state, **extra):
        value = {'schema_version': 1, 'job_id': args.job_id,
                 'module': config.task, 'state': state,
                 'utc': datetime.now(timezone.utc).isoformat(), **extra}
        publish_status(args.status, value)
    def monitor():
        while not finished.wait(.1):
            if args.stop.exists() or not parent_alive(args.parent_pid):
                stopped.set()
                for hook in tuple(stop_hooks): hook()
                return
    threading.Thread(target=monitor, daemon=True).start()
    publish('loading')
    try:
        if args.stop.exists() or not parent_alive(args.parent_pid):
            stopped.set()
        if stopped.is_set():
            publish('cancelled')
            return 2
        if config.task == 'fishing':
            from auto_fishing import enable_dpi_awareness, run
            enable_dpi_awareness()
            count = {'catches': 0}
            def progress(value):
                count.update(value)
                publish('running', progress=value)
            target = config.target
            if target is None: raise ValueError('Fishing requires a finite catch target')
            options = argparse.Namespace(window_title='hololive-Dreams', language='zh-TW',
                fps=40., lead=.20, deadband=.018, pulse_hz=7., once=False,
                target_streak=0, target_catches=target, stop_hotkey='INSERT',
                max_seconds=600., debug_dir=str(args.status.parent / 'diagnostics'), record=False)
            def status(text):
                print(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
                                  'event': 'upstream_status', 'message': text}, ensure_ascii=False), flush=True)
            run(options, stop_event=stopped, status_callback=status,
                progress_callback=progress)
            if stopped.is_set():
                publish('cancelled', completed=count['catches'])
                return 2
            if count['catches'] < target: raise RuntimeError('Catch target was not reached')
            publish('complete', completed=count['catches'])
        elif config.task == 'jump_rope':
            if config.rounds != 1:
                raise ValueError('Jump worker requires one separately authorized round')
            from batch_session import BatchSession, SessionConfig
            from jump_rope_bot import JumpRopeBot
            from detector import FullFrameDetector
            from focus_recovery import FocusRecovery
            def cancelled():
                return stopped.is_set() or args.stop.exists() or not parent_alive(args.parent_pid)
            detector = FullFrameDetector(backend='directml', stopped=cancelled)
            authorization = {'ready': False, 'error': None}
            def capture_event(event):
                if event.get('event') != 'first_frame' or authorization['ready']:
                    return
                authorization['ready'] = True
                publish('ready', phase='capture_ready', backend=detector.backend)
                try:
                    wait_for_start(args.status.parent / 'start.json', args.job_id, cancelled)
                    publish('running', phase='authorized')
                except Exception as error:
                    # FocusRecovery records and swallows sink exceptions. Set
                    # the actual stop state before returning from that callback.
                    authorization['error'] = error
                    stopped.set()
                    session.stop()
            focus = FocusRecovery(diagnostics=True, sink=capture_event)
            def make_bot(options, **kwargs):
                upstream_stop = kwargs.pop('stop_requested')
                return JumpRopeBot(replace(options, auto_start=False), detector=detector,
                    focus_recovery=focus, diagnostic_session=session.diagnostics,
                    diagnostic_round=1, stop_requested=lambda: cancelled() or upstream_stop(), **kwargs)
            session = BatchSession(SessionConfig(rounds=config.rounds, stop_hotkey='INSERT',
                diagnostics=True, score_limit=config.score_limit),
                bot_factory=make_bot, base=args.status.parent / 'diagnostics',
                notify=lambda kind, value: print(json.dumps({'event': kind, 'value': value}, default=str), flush=True))
            stop_hooks.append(session.stop)
            if stopped.is_set(): session.stop()
            session.run()
            if authorization['error'] is not None:
                raise RuntimeError(str(authorization['error'])) from authorization['error']
            if not authorization['ready']:
                raise RuntimeError('Jump capture never reached readiness')
            if session.stop_reason == 'stopped':
                publish('cancelled', completed=session.completed_rounds)
                return 2
            if session.completed_rounds != config.rounds:
                raise RuntimeError('Round target was not reached')
            publish('complete', completed=session.completed_rounds,
                    diagnostic_directory=str(session.directory))
        elif config.task == 'cooking':
            if config.rounds != 1:
                raise ValueError('Cooking worker requires one separately authorized round')
            os.environ['COOKING_STOP_FILE'] = str(args.stop)
            os.environ['COOKING_STOP_KEY'] = 'INSERT'
            os.environ['COOKING_TOGGLE_FILE'] = str(args.status.parent / 'toggle')
            stop_hooks.append(lambda: args.stop.touch(exist_ok=True))
            from daily.cooking_runtime import load_cooking
            model, assets, warmup = load_cooking(source)
            from standalone import Desktop, play
            import diagnostic_store as diagnostics
            directory = args.status.parent / 'diagnostics'
            store = diagnostics.configure(directory, True, module='cooking',
                                          models=assets, warmup_ms=warmup)
            try:
                desktop = Desktop('hololive-Dreams')
                try:
                    if desktop.capture() is None:
                        raise RuntimeError('Cooking capture is not ready in the foreground')
                    publish('ready', phase='capture_ready', backend=model.runtime.backend)
                    wait_for_start(args.status.parent / 'start.json', args.job_id,
                        lambda: stopped.is_set() or args.stop.exists() or not parent_alive(args.parent_pid))
                    publish('running', phase='authorized')
                    deadline = time.monotonic()+60
                    while True:
                        if stopped.is_set() or args.stop.exists() or not parent_alive(args.parent_pid):
                            raise InterruptedError('Cooking start cancelled')
                        frame = desktop.capture()
                        if frame is None:
                            raise RuntimeError('Cooking lost foreground before the order screen')
                        canonical, _, _ = model.normalize(frame)
                        phase = model.screen_state.classify(canonical)['phase']
                        if phase in {'order', 'playing'}:
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Cooking order screen did not appear after authorized start')
                        time.sleep(.1)
                finally:
                    desktop.close()
                if stopped.is_set() or args.stop.exists():
                    raise InterruptedError('Cooking start cancelled')
                options = argparse.Namespace(title='hololive-Dreams', output=directory,
                    fps=20, seconds=420, diagnostic_lossless_interval=2.)
                result = play(options, model, autostart=True,
                              status_callback=lambda phase: publish('running', phase=phase))
                if stopped.is_set() or args.stop.exists():
                    publish('cancelled', completed=0)
                    return 2
                reading = result.get('result', {})
                if not result.get('natural_end') or reading.get('verified') is not True:
                    raise RuntimeError('Cooking did not confirm natural completion and result numbers')
            finally:
                store.finish()
            publish('complete', completed=1, result=reading,
                    diagnostic_directory=str(directory))
        else:
            # High & Low requires a native configurable cap before integration.
            raise RuntimeError('Module integration is not enabled yet')
        return 0
    except Exception as error:
        publish('failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        finished.set()


if __name__ == '__main__':
    raise SystemExit(main())
