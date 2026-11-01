import json
from pathlib import Path
import sys

import pytest

from daily.contracts import TaskConfig
from daily.workers import WorkerError, WorkerSupervisor


def supervisor(tmp_path, code):
    worker = tmp_path / 'fixture.py'
    worker.write_text(code, encoding='utf-8')
    return WorkerSupervisor(tmp_path, Path(sys.executable), tmp_path / 'sessions',
        command_factory=lambda c, s, stop, job: [sys.executable, str(worker), str(s), str(stop), job])


def test_zero_exit_without_result_is_not_success(tmp_path):
    runner = supervisor(tmp_path, 'pass\n')
    with pytest.raises(WorkerError, match='未回報完成證據'):
        runner.run(TaskConfig('fishing', target=3))
    assert runner.process is None


def test_stale_job_status_is_rejected(tmp_path):
    runner = supervisor(tmp_path, "import sys,json,pathlib\npathlib.Path(sys.argv[1]).write_text(json.dumps({'job_id':'old','state':'complete','completed':3}))\n")
    with pytest.raises(WorkerError): runner.run(TaskConfig('fishing', target=3))


def test_valid_worker_handshake(tmp_path):
    runner = supervisor(tmp_path, "import sys,json,pathlib\npathlib.Path(sys.argv[1]).write_text(json.dumps({'job_id':sys.argv[3],'state':'complete','completed':3}))\n")
    assert runner.run(TaskConfig('fishing', target=3)).completed == 3


def test_cooperative_timeout_keeps_partial_result(tmp_path):
    runner = supervisor(tmp_path, "import sys,pathlib,time\nwhile not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)\n")
    with pytest.raises(WorkerError, match='逾時'):
        runner.run(TaskConfig('fishing', target=3), timeout=.1, stop_grace=1)
    assert runner.process is None


def test_live_is_never_sent_to_minigame_worker(tmp_path):
    runner = supervisor(tmp_path, 'pass\n')
    with pytest.raises(WorkerError, match='Live'):
        runner.run(TaskConfig('solo_live', rounds=1))


def test_recording_guard_failure_stops_worker_without_waiting_for_timeout(tmp_path):
    runner = supervisor(tmp_path, "import sys,pathlib,time\nwhile not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)\n")
    checks = []
    def guard():
        checks.append(True)
        if len(checks) > 2:
            raise RuntimeError('recording lost')
    runner.guard = guard
    with pytest.raises(RuntimeError, match='recording lost'):
        runner.run(TaskConfig('fishing', target=3), timeout=30, stop_grace=1)
    assert runner.process is None
    assert len(checks) == 3
    assert list((tmp_path / 'sessions').rglob('stop'))


def test_failed_initial_recording_guard_does_not_create_or_launch_worker(tmp_path):
    runner = supervisor(tmp_path, "raise RuntimeError('must not run')\n")
    def guard():
        raise RuntimeError('no recording')
    runner.guard = guard
    with pytest.raises(RuntimeError, match='no recording'):
        runner.run(TaskConfig('fishing', target=3))
    assert not (tmp_path / 'sessions').exists()


READY_WORKER = """import sys,pathlib,time,json
status,stop,job=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),sys.argv[3]
status.write_text(json.dumps({'job_id':job,'state':'ready'}))
token=status.parent/'start.json'
while not token.exists():
    if stop.exists():sys.exit(2)
    time.sleep(.01)
assert json.loads(token.read_text())['job_id']==job
status.write_text(json.dumps({'job_id':job,'state':'complete','completed':1}))
"""


def test_ready_worker_cannot_proceed_without_parent_preflight(tmp_path):
    runner = supervisor(tmp_path, READY_WORKER)
    with pytest.raises(WorkerError, match='preflight'):
        runner.run(TaskConfig('jump_rope', rounds=1), stop_grace=1)
    assert not list((tmp_path/'sessions').rglob('start.json'))


def test_parent_preflight_runs_once_before_authorizing_ready_worker(tmp_path):
    runner = supervisor(tmp_path, READY_WORKER)
    calls = []
    def ready(record):
        assert not list((tmp_path/'sessions').rglob('start.json'))
        calls.append(record['job_id'])
        return {'cost': 1}
    assert runner.run(TaskConfig('jump_rope', rounds=1), on_ready=ready).completed == 1
    assert len(calls) == 1
