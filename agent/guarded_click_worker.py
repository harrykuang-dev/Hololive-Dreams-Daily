"""Disposable local-controller click worker; currently not enabled by any task."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from daily.game_process import GameProcess, ParentProcess
from daily.guarded_click import ClickRequest, FrameCoordinates, GuardedClickError, execute_click
from daily.input_release import ManagedInputRelease
from daily.inputs import Inputs


PREFIX = 'DAILY_INPUT:'


def emit(state, **details):
    print(PREFIX+json.dumps({'state': state, **details}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    parent = game = inputs = None
    try:
        if config.get('exclusive_input') is not True:
            raise GuardedClickError('Managed input domain has not been reserved')
        parent = ParentProcess(config['parent_pid'], expected_creation=config['parent_creation'])
        game = GameProcess(config['hwnd'], expected_pid=config['game_pid'], expected_image=config['game_image'])
        def check_target():
            if (not parent.alive() or not game.backend.is_running(game.handle)
                    or game.backend.pid_for_window(config['hwnd']) != game.pid):
                raise GuardedClickError('Click target or owner changed')
        # Controller is local to THIS process. Terminating this worker removes
        # its queued jobs before independent emergency input release.
        from maa.controller import Win32Controller
        from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
        from maa.toolkit import Toolkit
        Toolkit.init_option(str(args.config.parent/'controller-debug'))
        controller = Win32Controller(config['hwnd'], MaaWin32ScreencapMethodEnum.FramePool,
            MaaWin32InputMethodEnum.Seize, MaaWin32InputMethodEnum.Seize)
        if not controller.set_screenshot_target_short_side(720):
            raise GuardedClickError('Local capture scaling is unavailable')
        if not controller.post_connection().wait().succeeded:
            raise GuardedClickError('Local input controller did not connect')
        coordinates = FrameCoordinates(controller)
        def capture():
            check_target()
            result = controller.post_screencap().wait()
            if not result.succeeded:
                raise GuardedClickError('Local controller capture failed')
            coordinates.update_frame(result.get().shape)
            check_target()
        capture()
        release = ManagedInputRelease()
        if not release.released():
            raise GuardedClickError('Managed inputs are already held before acquisition')
        inputs = Inputs(coordinates, guard=check_target)
        sequence = 0
        emit('ready', job_id=config['job_id'], game_pid=game.pid, game_image=game.image,
             capture_width=coordinates.width, capture_height=coordinates.height)
        while True:
            line = sys.stdin.readline(2049)
            if not line:
                return 0
            if len(line) > 2048 or not line.endswith('\n'):
                raise GuardedClickError('Oversized input request')
            request = ClickRequest.parse(json.loads(line), config['job_id'],
                                         last_sequence=sequence, now=time.monotonic())
            capture()  # Refresh SDK coordinates if the window was resized.
            check_target()
            execute_click(request, inputs, clock=time.monotonic, check_target=check_target)
            if not release.released():
                raise GuardedClickError('Managed inputs remain held after click')
            sequence = request.sequence
            emit('complete', job_id=config['job_id'], sequence=sequence,
                 captured_at=request.captured_at, released=True)
    except Exception as error:
        emit('failed', job_id=config.get('job_id'), error=f'{type(error).__name__}: {error}')
        return 1
    finally:
        try:
            if inputs is not None:
                inputs.release_all()
        finally:
            try:
                if game is not None:
                    game.close()
            finally:
                if parent is not None:
                    parent.close()


if __name__ == '__main__':
    raise SystemExit(main())
