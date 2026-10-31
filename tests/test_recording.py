import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from daily.inputs import Inputs
from daily.recording import FrameRecording as GameRecording, RecordingError
from daily.recording import GameRecording as ProcessRecording


class CaptureController:
    def __init__(self, fail_after=None):
        self.count = 0
        self.fail_after = fail_after
        self.inputs = []

    def post_screencap(self):
        self.count += 1
        succeeds = self.fail_after is None or self.count <= self.fail_after
        frame = np.full((720, 1280, 3), min(255, self.count * 20), np.uint8)
        return SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=succeeds, get=lambda: frame))

    def post_key_down(self, key):
        self.inputs.append(key)
        raise AssertionError('No game input expected')


def test_recording_decodes_and_timeline_preserves_action_time(tmp_path):
    controller = CaptureController()
    recorder = GameRecording(controller, tmp_path, 'task')
    recorder.start()
    recorder.mark('test_action', step='entry')
    time.sleep(.23)
    recorder.check()
    recorder.stop()
    cap = cv2.VideoCapture(str(recorder.video))
    try:
        assert cap.isOpened()
        frames = []
        while True:
            ok, image = cap.read()
            if not ok:
                break
            frames.append(image)
        assert len(frames) == recorder.frames >= 2
        assert frames[-1].mean() > frames[0].mean()
    finally:
        cap.release()
    timeline = [json.loads(line) for line in recorder.timeline.read_text(encoding='utf-8').splitlines()]
    events = [item['event'] for item in timeline]
    assert events.index('recording_ready') < events.index('test_action') < events.index('recording_stopped')
    assert timeline[-1]['frames'] == len(frames)
    assert not controller.inputs


def test_no_frame_cannot_authorize_input(tmp_path):
    recorder = GameRecording(CaptureController(fail_after=0), tmp_path, 'failed')
    with pytest.raises(RecordingError):
        recorder.start()
    inputs = Inputs(recorder.controller, guard=recorder.check)
    with pytest.raises(RecordingError):
        inputs.click(1, 2)
    assert not recorder.controller.inputs


def test_capture_failure_blocks_more_input_but_keeps_partial_video(tmp_path):
    recorder = GameRecording(CaptureController(fail_after=1), tmp_path, 'interrupted')
    recorder.start()
    deadline = time.monotonic() + 2
    while recorder._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    with pytest.raises(RecordingError):
        Inputs(recorder.controller, guard=recorder.check).click(1, 2)
    with pytest.raises(RecordingError):
        recorder.stop()
    assert recorder.video.stat().st_size > 0
    assert not recorder.controller.inputs


def test_stale_recording_blocks_input(tmp_path):
    recorder = GameRecording(CaptureController(), tmp_path, 'stale')
    recorder.start()
    try:
        recorder._last_frame = time.monotonic() - 4
        with pytest.raises(RecordingError, match='三秒'):
            Inputs(recorder.controller, guard=recorder.check).click(1, 2)
        assert not recorder.controller.inputs
    finally:
        recorder.stop()


def test_process_recording_stop_verifies_worker_and_preserves_paths(tmp_path):
    recorder = ProcessRecording(SimpleNamespace(info={'type': 'win32', 'hwnd': 123}), tmp_path, 'test')
    recorder._capture_paths = {'video': str(recorder.directory / 'game.avi'),
                               'timeline': str(recorder.directory / 'game.jsonl')}
    recorder.status_file.write_text(json.dumps({'job_id': recorder.job_id, 'state': 'complete', 'frames': 5}))
    recorder._process = SimpleNamespace(wait=lambda **kwargs: None, returncode=0)
    recorder.stop()
    final = json.loads(recorder.timeline.read_text(encoding='utf-8').splitlines()[-1])
    assert final['event'] == 'recording_stopped' and final['frames'] == 5
    assert final['frame_timeline'].endswith('game.jsonl')


@pytest.mark.parametrize('state,age', [('failed', 0), ('ready', 4), ('ready', -1)])
def test_process_recording_health_blocks_failed_or_stale_worker(tmp_path, state, age):
    recorder = ProcessRecording(SimpleNamespace(info={'type': 'win32', 'hwnd': 123}), tmp_path, 'test')
    recorder._process = SimpleNamespace(poll=lambda: None)
    recorder.status_file.write_text(json.dumps({'job_id': recorder.job_id, 'state': state,
                                                'updated_monotonic': time.monotonic() - age}))
    with pytest.raises(RecordingError):
        recorder.check()
