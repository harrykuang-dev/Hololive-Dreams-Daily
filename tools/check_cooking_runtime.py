"""Verify pinned assets and warm cooking inference without capture or input."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from daily.cooking_runtime import load_cooking


def check(module: Path, samples):
    model, identities, timings = load_cooking(module)
    import cv2
    observations = []
    for path in samples:
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError('Cannot decode sample: '+path.name)
        started = time.perf_counter()
        observation = model.analyze(frame, fast=True)
        if observation['phase'] not in {'order', 'playing', 'results'}:
            raise ValueError('Cooking reference phase was not recognized: '+path.name)
        observations.append({'sample': path.name, 'phase': observation['phase'],
            'recipe': observation.get('recipe'), 'candidates': len(observation.get('candidates', [])),
            'inference_ms': (time.perf_counter()-started)*1000})
    return {'runtime': model.runtime.info, 'assets': identities, 'warmup_ms': timings,
            'samples': observations, 'maa_loaded': False, 'game_inputs': 0,
            'versions': {name: importlib.metadata.version(name) for name in
                         ('numpy', 'opencv-python', 'onnxruntime-directml', 'scipy', 'moderngl')}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', type=Path, default=ROOT / 'upstream/holodori/upstream/cooking')
    parser.add_argument('--sample', action='append', type=Path, default=[])
    args = parser.parse_args()
    print(json.dumps(check(args.module, args.sample), ensure_ascii=False, indent=2))
