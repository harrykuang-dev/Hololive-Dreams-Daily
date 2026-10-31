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
    'solo_setup': [1020, 100, 200, 60],
    'multi_ready': [585, 12, 190, 45],
    'song_select': [680, 678, 290, 30],
    'fishing_setup_second': [790, 95, 330, 45],
    'fishing_map_second': [20, 12, 285, 78],
    'holowork': [12, 8, 230, 75],
    'minigame_portal': [12, 8, 230, 75],
    'cooking_setup': [15, 15, 300, 175],
    'jump_setup': [15, 15, 300, 175],
    'notifications': [26, 32, 110, 50],
    'daily_missions': [12, 8, 240, 75],
    'achievements': [12, 8, 240, 75],
    'event_missions': [12, 8, 240, 75],
    'gifts': [12, 8, 240, 75],
}
AUTO = {'solo_auto_on': ('solo_setup', [523, 635, 77, 41]),
        'multi_auto_on': ('multi_ready', [217, 632, 77, 41]),
        'map_fishing_hook': ('fishing_map_second', [330, 433, 55, 58]),
        'map_clock': ('fishing_map_second', [365, 114, 60, 58]),
        'map_shop': ('fishing_map_second', [275, 249, 58, 57]),
        'map_card_close': ('fishing_map_popup', [1165, 108, 55, 55])}


def build(samples: Path):
    destination = ROOT / 'resource/image/anchors'
    destination.mkdir(parents=True, exist_ok=True)
    entries = {}
    records = json.loads((samples / 'manifest.json').read_text(encoding='utf-8'))
    provenance = {record['scene']: record for record in records}
    for name, (scene, roi) in {
        **{scene: (scene, roi) for scene, roi in ANCHORS.items()}, **AUTO}.items():
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
        elif name.startswith('map_'):
            entries[name].update(scene=False)
        if name == 'notifications':
            entries[name]['priority'] = 1
    (ROOT / 'resource/anchors.json').write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Built {len(entries)} UI anchors.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=Path, required=True)
    build(parser.parse_args().samples)
