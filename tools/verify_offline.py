"""Run the real Maa OCR pipeline against recordings, with no live controller."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from dataclasses import asdict

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from daily.vision import Vision
from daily.navigation import Navigator, Observation
from daily.resources import EnergySettings
from daily.live import LiveControls
from daily.holowork import HoloWorkRunner
from offline_controller import OfflineController


class InspectRecordings(CustomAction):
    def __init__(self, samples):
        super().__init__()
        self.samples = samples
        self.report = []

    def run(self, context, argv):
        try:
            return self.inspect(context, argv)
        except Exception as error:
            self.report.append({'error': f'{type(error).__name__}: {error}'})
            return False

    def inspect(self, context, argv):
        vision = Vision(ROOT / 'resource')
        navigator = Navigator(context, ROOT / 'resource', self.samples.parent / 'offline-evidence')
        for path in sorted(self.samples.glob('*.png')):
            frame = cv2.imread(str(path))
            scene = navigator.interpret(frame).scene
            expected = {'auto_playing': 'live_playing', 'cooking_energy': 'energy_settings',
                        'energy_controls': 'energy_settings', 'fishing_map_initial': 'fishing_map_second',
                        'fishing_map_popup': 'fishing_map_second', 'holowork_dispatch': 'holowork',
                        'holowork_summary': 'holowork', 'holowork_assignment_empty': 'holowork',
                        'holowork_assignment': 'holowork', 'holowork_working': 'holowork'}.get(path.stem, path.stem)
            result = {'sample': path.stem, 'scene': scene,
                      'expected_scene': expected, 'matching_scene': scene == expected}
            if path.stem in ('solo_setup', 'multi_ready'):
                roi = [420, 588, 180, 40] if path.stem == 'solo_setup' else [145, 585, 150, 40]
                captured = time.monotonic()
                detail = context.run_recognition('DailyOCR', frame,
                    {'DailyOCR': {'recognition': 'OCR', 'roi': roi, 'expected': ['.*'], 'threshold': .5}})
                text = ''.join(getattr(item, 'text', '') for item in detail.filtered_results) if detail else ''
                observation = vision.auto(frame, scene, text, captured_at=captured)
                result.update(auto_text=text, auto_enabled=observation.enabled,
                              auto_remaining=observation.remaining)
                result['live_state'] = asdict(LiveControls(navigator).read(
                    Observation(frame, scene, time.monotonic())))
            if scene == 'energy_settings':
                result['energy_selection'] = asdict(EnergySettings(navigator).read(
                    Observation(frame, scene, time.monotonic())))
            if scene == 'live_result':
                result['auto_result'] = bool(navigator.text(Observation(frame, scene, time.monotonic()),
                                                           '^AUTO LIVE$', (650, 180, 530, 330)))
            if path.stem.startswith('holowork'):
                work = HoloWorkRunner(navigator).read(Observation(frame, scene, time.monotonic()))
                result['holowork'] = {'kind': work.kind, 'scores': work.scores,
                                      'empty_slots': work.empty_slots, 'timers': work.timers}
                expected_kind = {'holowork': 'completed', 'holowork_dispatch': 'available',
                                 'holowork_summary': 'summary', 'holowork_get': 'get',
                                 'holowork_assignment_empty': 'assignment',
                                 'holowork_assignment': 'assignment', 'holowork_started': 'started',
                                 'holowork_working': 'working'}[path.stem]
                if work.kind != expected_kind:
                    raise ValueError(f'{path.stem}: expected {expected_kind}, got {work.kind}')
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
    anchors = Vision(ROOT / 'resource').anchors
    if any(not row['matching_scene'] for row in action.report
           if row['expected_scene'] in anchors and anchors[row['expected_scene']].get('scene', True)):
        raise SystemExit('A positive scene reference did not match')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.samples, args.output)
