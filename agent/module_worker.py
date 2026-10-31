"""One upstream module per process; no module imports occur during parent startup."""
from __future__ import annotations

import argparse
from dataclasses import asdict
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
            from batch_session import BatchSession, SessionConfig
            session = BatchSession(SessionConfig(rounds=config.rounds, stop_hotkey='INSERT',
                diagnostics=True, score_limit=config.score_limit),
                notify=lambda kind, value: print(json.dumps({'event': kind, 'value': value}, default=str), flush=True))
            stop_hooks.append(session.stop)
            if stopped.is_set(): session.stop()
            session.run()
            if session.stop_reason == 'stopped':
                publish('cancelled', completed=session.completed_rounds)
                return 2
            if session.completed_rounds != config.rounds:
                raise RuntimeError('Round target was not reached')
            publish('complete', completed=session.completed_rounds,
                    diagnostic_directory=str(session.directory))
        else:
            # Cooking and High & Low need per-round resource gating and native
            # configurable caps respectively before enabling their workers.
            raise RuntimeError('Module integration is not enabled yet')
        return 0
    except Exception as error:
        publish('failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        finished.set()


if __name__ == '__main__':
    raise SystemExit(main())
