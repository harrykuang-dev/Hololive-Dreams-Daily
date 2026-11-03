"""One independently supervised game process per multiplayer Auto guard."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from daily.auto_watch import watch
from daily.processes import publish_status
from daily.game_process import ParentProcess
from daily.input_lane import InputLane
from daily.watch_mailbox import WatchMailbox


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    directory = args.config.parent
    def publish(state, **details):
        publish_status(directory/'status.json', {'job_id': config['job_id'], 'state': state,
            'utc': datetime.now(timezone.utc).isoformat(), 'updated_monotonic': time.monotonic(), **details})
    parent = None
    channel = None
    try:
        parent = ParentProcess(config['parent_pid'], expected_creation=config['parent_creation'])
        channel = WatchMailbox(directory, config['job_id'], publish)
        channel.start()
        def make_lane(identity):
            keys = ('job_id', 'hwnd', 'game_pid', 'game_image')
            return InputLane(ROOT, Path(sys.executable), directory/'input',
                             {key: identity[key] for key in keys}, exclusive_input=True)
        return watch(config, channel, alive=parent.alive,
                     lane_factory=make_lane if config.get('input_protocol') == 1 else None)
    except Exception as error:
        publish('failed', error=f'{type(error).__name__}: {error}')
        return 1
    finally:
        if parent is not None:
            parent.close()
        if channel is not None:
            channel.finish()


if __name__ == '__main__':
    raise SystemExit(main())
