"""Load and warm the pinned cooking models inside their isolated interpreter."""
from pathlib import Path
import sys
import time

from .module_assets import model_files


def load_cooking(module: Path):
    identities = model_files(module)
    sys.path.insert(0, str(module.resolve() / 'outputs/kitchen-bot'))
    from perception import Perception
    from supplemental_food import SelectedSupplement
    from inference_backend import model_path
    import numpy as np
    model = Perception('point-classifier-resnet18.onnx', backend='directml')
    model.supplement = SelectedSupplement(model, model_path('segmenter-positive-surfaces'))
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        blank = np.zeros((1, 3, 280, 448), dtype=np.float32)
        for segmenter in (model.model, model.supplement.model.model):
            segmenter.infer(blank, ('labels', 'confidence'))
        for classifier in (model.classifier, model.point_classifier):
            classifier.classify([np.zeros((64, 64, 3), dtype=np.uint8)])
        timings.append((time.perf_counter()-started)*1000)
    if model.runtime.backend != 'directml':
        raise RuntimeError('Cooking DirectML warmup failed: '+str(model.runtime.info))
    if any(name == 'maa' or name.startswith('maa.') for name in sys.modules):
        raise RuntimeError('Cooking inference must remain separate from Maa')
    return model, identities, timings
