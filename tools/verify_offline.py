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
from daily.park import ParkRunner, GIFT_PLACES, LOTTERY_PLACE
from daily.fishing import FishingRunner
from daily.rewards import RewardsRunner
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
            expected = {'daily_claimable': 'daily_missions', 'pass_claimable': 'daily_missions',
                        'pass_empty': 'daily_missions', 'rewards_get': 'daily_missions',
                        'achievements_claimable': 'achievements', 'event_claimable': 'event_missions',
                        'gifts_empty': 'gifts', 'reward_season_empty_native': 'daily_missions',
                        'event_free': 'event_missions', 'arena_map': 'park_map',
                        'arena_popup': 'park_map', 'high_low_entry': 'home',
                        'login_reward': 'holowork_get', 'live_reward': 'holowork_get',
                        'lottery_entry_native': 'home',
                        'lottery_dialogue_native': 'home', 'gift_house_unreached_native': 'home',
                        'gift_house_wall_native': 'home', 'live_achievement_native': 'live_achievement',
                        'live_reward_transition': 'holowork_get'}.get(path.stem, expected)
            result = {'sample': path.stem, 'scene': scene,
                      'expected_scene': expected, 'matching_scene': scene == expected}
            if path.stem == 'date_changed':
                button = navigator.date_change_button(Observation(frame, scene, time.monotonic()))
                if scene != 'date_changed' or button is None:
                    raise ValueError('Date-change notice did not have its unique confirmation')
                result['date_confirmation'] = list(button)
            if path.stem == 'login_bonus':
                button = navigator.login_bonus_button(Observation(frame, scene, time.monotonic()))
                if scene != 'login_bonus' or button is None:
                    raise ValueError('Observed login-bonus page did not have its exact continue hint')
                result['bonus_continue'] = list(button)
            if path.stem in {'login_reward', 'live_reward', 'live_reward_transition'}:
                button = navigator.received_reward_button(Observation(frame, scene, time.monotonic()))
                if scene != 'holowork_get' or button is None:
                    raise ValueError('Login reward did not have its received/close confirmation')
                result['reward_close'] = list(button)
            if path.stem == 'login_bonus_standard':
                button = navigator.standard_login_bonus_button(Observation(frame, scene, time.monotonic()))
                if scene != 'login_bonus_standard' or button is None:
                    raise ValueError('Standard login-bonus page lacked its title and continue hint')
                result['bonus_continue'] = list(button)
            if path.stem == 'login_notice':
                button = navigator.login_notice_button(Observation(frame, scene, time.monotonic()))
                if scene != 'login_notice' or button is None:
                    raise ValueError('Observed membership announcement was not uniquely recognized')
                result['notice_close'] = list(button)
            if path.stem in {'live_achievement', 'live_achievement_native'}:
                button = navigator.live_achievement_button(Observation(frame, scene, time.monotonic()))
                if scene != 'live_achievement' or button is None:
                    raise ValueError('Live achievement details were not uniquely recognized')
                result['achievement_close'] = list(button)
            if path.stem in {'gift_house_entry', 'gift_house_unreached_native', 'gift_house_wall_native'}:
                markers = vision.find_all(frame, 'park_character_gift_marker', roi=(300, 150, 800, 300))
                expected_count = 0 if path.stem == 'gift_house_wall_native' else 1
                if len(markers) != expected_count:
                    raise ValueError(f'{path.stem}: expected {expected_count} gift markers, got {len(markers)}')
                result['gift_markers'] = [list(marker.box) for marker in markers]
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
            if path.stem == 'fishing_setup_second':
                FishingRunner(navigator, ROOT, self.samples.parent).verify_practice_bait(
                    Observation(frame, scene, time.monotonic()))
                result['practice_bait_verified'] = True
            if path.stem == 'fishing_result':
                result['cancel_buttons'] = len(navigator.text(
                    Observation(frame, scene, time.monotonic()), r'^[×xX✕]?\s*取消$', (720, 620, 230, 80)))
                if result['cancel_buttons'] != 1:
                    raise ValueError('Fishing result cancel is not unique')
            if path.stem == 'arena_popup':
                observation = Observation(frame, scene, time.monotonic())
                for text, roi in [('^比大小$', (780, 180, 450, 170)),
                                  ('^Shiori的卡牌遊戲桌$', (810, 400, 390, 90)),
                                  ('^移動$', (780, 480, 450, 100))]:
                    if len(navigator.text(observation, text, roi)) != 1:
                        raise ValueError('High & Low map card is not uniquely verified: '+text)
            reward_expected = {
                'daily_claimable': ('daily', 'claimable'), 'daily_missions': ('daily', 'empty'),
                'reward_season_empty_native': ('season', 'empty'),
                'pass_claimable': ('pass', 'claimable'), 'pass_empty': ('pass', 'empty'),
                'achievements_claimable': ('achievements', 'claimable'),
                'achievements': ('achievements', 'empty'),
                'gifts': ('gifts', 'claimable'), 'gifts_empty': ('gifts', 'empty')}.get(path.stem)
            reward_expected = {'event_claimable': ('event_daily', 'claimable'),
                               'event_missions': ('event_daily', 'empty'),
                               'event_free': ('event_free', 'empty')}.get(path.stem, reward_expected)
            if reward_expected:
                view = RewardsRunner(navigator).read(Observation(frame, scene, time.monotonic()))
                result['rewards'] = {'page': view.page, 'state': view.state}
                if (view.page, view.state) != reward_expected:
                    raise ValueError(f'{path.stem}: expected {reward_expected}, got {view.page}/{view.state}')
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
            park_reference = {
                'gift_house_entry': (GIFT_PLACES[0], 'entry'),
                'gift_house_result': (GIFT_PLACES[0], 'unknown'),
                'gift_bijou_entry': (GIFT_PLACES[1], 'entry'),
                'gift_bijou_dialogue': (GIFT_PLACES[1], 'dialogue'),
                'gift_bijou_result': (GIFT_PLACES[1], 'unknown'),
                'gift_azki_approach': (GIFT_PLACES[2], 'park'),
                'gift_azki_dialogue': (GIFT_PLACES[2], 'dialogue'),
                'gift_azki_result': (GIFT_PLACES[2], 'reward'),
                'lottery_entry': (LOTTERY_PLACE, 'entry'),
                'lottery_entry_native': (LOTTERY_PLACE, 'entry'),
                'lottery_dialogue': (LOTTERY_PLACE, 'dialogue'),
                'lottery_dialogue_native': (LOTTERY_PLACE, 'dialogue'),
                'lottery_scratch': (LOTTERY_PLACE, 'scratch'),
                'lottery_result': (LOTTERY_PLACE, 'reward'),
            }.get(path.stem)
            if park_reference:
                place, expected_kind = park_reference
                park = ParkRunner(navigator).read(Observation(frame, scene, time.monotonic()), place)
                result['park'] = {'kind': park.kind, 'signature': park.signature}
                if park.kind != expected_kind:
                    raise ValueError(f'{path.stem}: expected {expected_kind}, got {park.kind}')
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
