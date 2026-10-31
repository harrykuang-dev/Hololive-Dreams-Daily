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
