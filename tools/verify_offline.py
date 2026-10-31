"""Run the real Maa OCR pipeline against recordings, with no live controller."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from daily.vision import Vision
from offline_controller import OfflineController


class InspectRecordings(CustomAction):
    def __init__(self, samples):
        super().__init__()
        self.samples = samples
        self.report = []

    def run(self, context, argv):
        vision = Vision(ROOT / 'resource')
        for path in sorted(self.samples.glob('*.png')):
            frame = cv2.imread(str(path))
            scene = vision.scene(frame)
            result = {'sample': path.stem, 'scene': scene,
                      'matching_scene': scene == path.stem}
            if path.stem in ('solo_setup', 'multi_ready'):
                roi = [420, 588, 180, 40] if path.stem == 'solo_setup' else [145, 585, 150, 40]
                captured = time.monotonic()
                detail = context.run_recognition('DailyOCR', frame,
                    {'DailyOCR': {'recognition': 'OCR', 'roi': roi, 'expected': ['.*'], 'threshold': .5}})
                text = ''.join(getattr(item, 'text', '') for item in detail.filtered_results) if detail else ''
                observation = vision.auto(frame, scene, text, captured_at=captured)
                result.update(auto_text=text, auto_enabled=observation.enabled,
                              auto_remaining=observation.remaining)
            self.report.append(result)
        return True


def verify(samples, output):
    Toolkit.init_option(str(output.parent / 'maa-debug'))
    resource = Resource()
    action = InspectRecordings(samples)
    resource.register_custom_action('InspectRecordings', action)
    if not resource.post_bundle(ROOT / 'resource').wait().succeeded:
        raise RuntimeError('Maa resource loading failed')
    controller = OfflineController(cv2.imread(str(samples / 'home.png')))
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError('Offline controller failed')
    tasker = Tasker()
    if not tasker.bind(resource, controller): raise RuntimeError('Maa bind failed')
    job = tasker.post_task('InspectRecordings', {'InspectRecordings': {
        'recognition': 'DirectHit', 'action': 'Custom', 'custom_action': 'InspectRecordings'}}).wait()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'maa_success': job.succeeded,
                                  'input_attempts': controller.input_attempts, 'samples': action.report},
                                  ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(action.report, ensure_ascii=False, indent=2))
    if not job.succeeded or controller.input_attempts: raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.samples, args.output)
