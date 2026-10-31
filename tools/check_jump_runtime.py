"""No-input model warmup in the isolated jump-rope interpreter."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys


def check(source: Path, samples=()):
    source = source.resolve()
    sys.path.insert(0, str(source))
    # Importing the batch module validates its dependencies; never instantiate
    # BatchSession, GameCapture or any input/controller object here.
    import batch_session  # noqa: F401
    from detector import FullFrameDetector
    import cv2
    model = source / 'models/rope.directml.onnx'
    if not model.is_file() or model.stat().st_size < 1000000:
        raise ValueError('Pinned DirectML model is missing or is an LFS pointer')
    detector = FullFrameDetector(backend='directml')
    if 'maa' in sys.modules or any(name.startswith('maa.') for name in sys.modules):
        raise RuntimeError('The minigame runtime must remain separate from Maa')
    observations = []
    for sample in samples:
        frame = cv2.imread(str(sample))
        if frame is None:
            raise ValueError('Cannot decode sample: '+sample.name)
        observation = detector.observe(frame)
        if observation.rope_xyxy is None or observation.marker_xyxy is None:
            raise ValueError('Rope or player marker was not detected: '+sample.name)
        observations.append({'sample': sample.name, 'rope': observation.rope_xyxy,
                             'marker': observation.marker_xyxy, 'timing_ms': observation.timings_ms})
    return {'backend': detector.backend, 'maa_loaded': False,
            'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(),
            'versions': {name: importlib.metadata.version(name) for name in
                         ('numpy', 'opencv-python', 'Pillow', 'pywin32', 'dxcam', 'onnxruntime-directml')},
            'warmup_frames': len(detector.warmup_diagnostics),
            'last_timing_ms': detector.warmup_diagnostics[-1],
            'samples': observations, 'game_inputs': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', type=Path, default=Path(__file__).resolve().parents[1] /
                        'upstream/holodori/upstream/jump_rope')
    parser.add_argument('--sample', type=Path, action='append', default=[])
    args = parser.parse_args()
    print(json.dumps(check(args.module, args.sample), ensure_ascii=False, indent=2))
