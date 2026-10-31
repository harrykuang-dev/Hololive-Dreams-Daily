"""Build small UI anchors from game-only, calibrated recording frames."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
ANCHORS = {
    'home': [800, 24, 185, 85],
    'live_portal': [12, 8, 230, 75],
    'live_playing': [984, 20, 68, 35],
    'live_result': [12, 8, 230, 75],
    'solo_setup': [1020, 100, 200, 60],
    'multi_ready': [585, 12, 190, 45],
    'multi_portal': [12, 8, 230, 75],
    'song_select': [680, 678, 290, 30],
    'fishing_setup_second': [790, 95, 330, 45],
    'fishing_result': [670, 15, 140, 65],
    'fishing_map_second': [20, 12, 285, 78],
    'holowork': [12, 8, 230, 75],
    'holowork_get': [555, 40, 172, 65],
    'holowork_started': [350, 290, 520, 170],
    'minigame_portal': [12, 8, 230, 75],
    'cooking_setup': [15, 15, 300, 175],
    'jump_setup': [15, 15, 300, 175],
    'notifications': [26, 32, 110, 50],
    'energy_settings': [875, 105, 175, 38],
    'daily_missions': [12, 8, 240, 75],
    'achievements': [12, 8, 240, 75],
    'event_missions': [12, 8, 240, 75],
    'event_list': [80, 95, 170, 45],
    'event_home': [1190, 375, 55, 80],
    'gifts': [12, 8, 240, 75],
}
AUTO = {'solo_auto_on': ('solo_setup', [523, 635, 77, 41]),
        'multi_auto_on': ('multi_ready', [217, 632, 77, 41]),
        'map_fishing_hook': ('fishing_map_second', [330, 433, 55, 58]),
        'map_clock': ('fishing_map_second', [365, 114, 60, 58]),
        'map_shop': ('fishing_map_second', [275, 249, 58, 57]),
        'portal_go': ('minigame_portal', [175, 581, 164, 57]),
        'solo_boost_button': ('solo_setup', [904, 631, 46, 52]),
        'minigame_boost_button': ('cooking_setup', [901, 625, 56, 60]),
        'energy_minus': ('energy_controls', [800, 410, 38, 35]),
        'energy_plus': ('energy_controls', [1080, 411, 42, 40]),
        'map_card_close': ('fishing_map_popup', [1165, 108, 55, 55])}
AUTO['work_empty_slot'] = ('holowork_assignment_empty', [464, 155, 80, 80])
AUTO['park_dialogue_next'] = ('gift_azki_dialogue', [980, 674, 74, 32])
AUTO['park_interact_key'] = ('gift_house_entry', [808, 427, 32, 33])
AUTO['park_gift_prompt'] = ('gift_house_entry', [901, 433, 23, 25])
AUTO['park_gift_prompt_compact'] = ('gift_bijou_entry', [897, 432, 25, 27])
AUTO['lottery_scratch_card'] = ('lottery_scratch', [630, 245, 350, 115])
AUTO['fishing_practice_infinite'] = ('fishing_setup_second', [1128, 512, 35, 25])
AUTO['map_game_portal'] = ('arena_map', [817, 207, 65, 66])


def build(samples: Path):
    destination = ROOT / 'resource/image/anchors'
    destination.mkdir(parents=True, exist_ok=True)
    entries = {}
    records = json.loads((samples / 'manifest.json').read_text(encoding='utf-8'))
    provenance = {record['scene']: record for record in records}
    for name, (scene, roi) in {
        **{scene: (scene, roi) for scene, roi in ANCHORS.items()}, **AUTO}.items():
        scene = {'energy_settings': 'cooking_energy', 'live_playing': 'auto_playing'}.get(scene, scene)
        source = cv2.imread(str(samples / f'{scene}.png'))
        if source is None or source.shape[:2] != (720, 1280):
            raise ValueError(f'Invalid calibrated sample: {scene}')
        x, y, w, h = roi
        path = destination / f'{name}.png'
        crop = source[y:y+h, x:x+w]
        if float(crop.std()) < 8: raise ValueError(f'Uninformative anchor: {name}')
        if not cv2.imwrite(str(path), crop): raise OSError(path)
        entries[name] = {'file': f'anchors/{name}.png', 'roi': roi, 'threshold': 0.90,
                         'source': provenance[scene],
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if name == 'map_fishing_hook':
            entries[name].update(scene=False, threshold=.82)
        elif name.startswith(('map_', 'park_', 'lottery_')) or name in ('energy_minus', 'energy_plus', 'portal_go', 'work_empty_slot', 'fishing_practice_infinite') or name.endswith('_boost_button'):
            entries[name].update(scene=False)
        if name == 'notifications':
            entries[name]['priority'] = 1
        elif name in ('event_list', 'event_home'):
            entries[name]['priority'] = 1
        elif name == 'energy_settings':
            entries[name]['priority'] = 2
    (ROOT / 'resource/anchors.json').write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Built {len(entries)} UI anchors.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=Path, required=True)
    build(parser.parse_args().samples)
