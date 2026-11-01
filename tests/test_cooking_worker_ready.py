"""Run the real cooking adapter with upstream fixtures that cannot send input."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from daily.contracts import TaskConfig
from daily.workers import WorkerError, WorkerSupervisor


def cooking_fixture(tmp_path):
    source = tmp_path/'modules/cooking'
    code = source/'outputs/kitchen-bot'
    code.mkdir(parents=True)
    asset = source/'models/model.onnx'
    asset.parent.mkdir()
    asset.write_bytes(b'verified fixture')
    (source/'models-manifest.json').write_text(json.dumps({'files': {'models/model.onnx': {
        'bytes': asset.stat().st_size, 'sha256': hashlib.sha256(asset.read_bytes()).hexdigest()}}}), encoding='utf-8')
    files = {
        'perception.py': '''from types import SimpleNamespace
class Perception:
 def __init__(self,*args,**kwargs):
  self.runtime=SimpleNamespace(backend='directml')
  self.model=SimpleNamespace(infer=lambda *a:None)
  self.classifier=self.point_classifier=SimpleNamespace(classify=lambda *a:None)
  self.screen_state=SimpleNamespace(classify=lambda frame:{'phase':'order'})
 def normalize(self,frame):return frame,0,1
''',
        'supplemental_food.py': '''from types import SimpleNamespace
class SelectedSupplement:
 def __init__(self,*args):self.model=SimpleNamespace(model=SimpleNamespace(infer=lambda *a:None))
''',
        'inference_backend.py': '''def model_path(name):return name
''',
        'diagnostic_store.py': '''from types import SimpleNamespace
def configure(*args,**kwargs):return SimpleNamespace(finish=lambda:None)
''',
        'standalone.py': '''import os
from pathlib import Path
class Desktop:
 def __init__(self,title):pass
 def capture(self):return object()
 def close(self):pass
def play(args,model,autostart,status_callback):
 assert autostart is True
 job=Path(os.environ['COOKING_STOP_FILE']).parent
 assert (job/'start.json').is_file()
 (job/'played').write_text('one round')
 return {'natural_end':True,'result':{'verified':True,'score':123,'completed_dishes':4}}
'''}
    for name, content in files.items():
        (code/name).write_text(content, encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    return WorkerSupervisor(root, Path(sys.executable), tmp_path/'jobs', module_root=tmp_path/'modules')


def test_cooking_waits_for_capture_ready_and_parent_token_before_one_round(tmp_path):
    worker = cooking_fixture(tmp_path)
    seen = []
    def ready(status):
        assert status['phase'] == 'capture_ready'
        assert not list((tmp_path/'jobs').rglob('played'))
        seen.append(status['job_id'])
        return {'cost':30}
    outcome = worker.run(TaskConfig('cooking', rounds=1), on_ready=ready, timeout=10)
    assert outcome.completed == 1 and outcome.evidence['result']['verified'] is True
    assert len(seen) == len(list((tmp_path/'jobs').rglob('played'))) == 1


def test_cooking_without_parent_preflight_never_enters_play(tmp_path):
    worker = cooking_fixture(tmp_path)
    with pytest.raises(WorkerError, match='preflight'):
        worker.run(TaskConfig('cooking', rounds=1), timeout=10, stop_grace=2)
    assert not list((tmp_path/'jobs').rglob('played'))
    assert worker.process is None


def test_cooking_batch_cannot_bypass_per_round_authorization(tmp_path):
    worker = cooking_fixture(tmp_path)
    with pytest.raises(WorkerError, match='one separately authorized round'):
        worker.run(TaskConfig('cooking', rounds=2), timeout=10)
    assert not list((tmp_path/'jobs').rglob('played'))
