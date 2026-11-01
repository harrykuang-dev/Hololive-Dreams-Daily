"""Exercise the real worker adapter with input-free upstream API fixtures."""
from pathlib import Path
import sys

import pytest

from daily.contracts import TaskConfig
from daily.workers import WorkerError, WorkerSupervisor


def fixture_worker(tmp_path):
    source = tmp_path / 'modules' / 'jump_rope'
    source.mkdir(parents=True)
    modules = {
        'detector.py': '''class FullFrameDetector:
 def __init__(self,backend,stopped):
  self.backend=backend
''',
        'focus_recovery.py': '''class FocusRecovery:
 def __init__(self,diagnostics,sink):self.sink=sink
 def note(self,event):
  try:self.sink(event)
  except Exception:pass
''',
        'jump_rope_bot.py': '''from dataclasses import dataclass
@dataclass
class BotConfig:
 auto_start:bool=True
class JumpRopeBot:
 def __init__(self,options,focus_recovery,stop_requested,**kwargs):
  self.options,self.focus,self.stopping=options,focus_recovery,stop_requested
 def run(self):
  assert self.options.auto_start is False
  self.focus.note({'event':'first_frame'})
  self.cancelled=self.stopping()
''',
        'batch_session.py': '''from dataclasses import dataclass
from jump_rope_bot import BotConfig
@dataclass
class SessionConfig:
 rounds:int
 stop_hotkey:str
 diagnostics:bool
 score_limit:int|None
class BatchSession:
 def __init__(self,config,bot_factory,base,notify):
  self.factory,self.directory=bot_factory,base
  self.diagnostics=None
  self.stopped=False
  self.completed_rounds=0
  self.stop_reason='not_started'
 def stop(self):self.stopped=True
 def run(self):
  bot=self.factory(BotConfig(),stop_requested=lambda:self.stopped)
  bot.run()
  self.stop_reason='stopped' if self.stopped or bot.cancelled else 'round_finished'
  if self.stop_reason=='round_finished':self.completed_rounds=1
'''}
    for name, text in modules.items():
        (source / name).write_text(text, encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    return WorkerSupervisor(root, Path(sys.executable), tmp_path / 'jobs',
                            module_root=tmp_path / 'modules')


def test_real_jump_adapter_waits_for_capture_ready_then_parent_authorization(tmp_path):
    runner = fixture_worker(tmp_path)
    seen = []
    def ready(status):
        assert status['phase'] == 'capture_ready'
        assert not list((tmp_path / 'jobs').rglob('start.json'))
        seen.append(status['job_id'])
        return {'cost': 0}
    assert runner.run(TaskConfig('jump_rope', rounds=1), on_ready=ready, timeout=10).completed == 1
    assert len(seen) == 1


def test_real_jump_adapter_stops_when_sink_cannot_obtain_authorization(tmp_path):
    runner = fixture_worker(tmp_path)
    with pytest.raises(WorkerError, match='preflight'):
        runner.run(TaskConfig('jump_rope', rounds=1), timeout=10, stop_grace=2)
    assert not list((tmp_path / 'jobs').rglob('start.json'))
    assert runner.process is None
