"""Bounded, isolated upstream execution with an explicit completion handshake."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .contracts import TaskConfig
from .inputs import Cancelled


class WorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerOutcome:
    completed: int
    evidence: dict


class WorkerSupervisor:
    def __init__(self, root: Path, python: Path, sessions: Path, *,
                 stopping=lambda: False, emergency_release=lambda: None,
                 command_factory=None, guard=lambda: None, progress=lambda record: None,
                 module_root=None):
        self.root, self.python, self.sessions = root, python, sessions
        self.stopping = stopping
        self.emergency_release = emergency_release
        self.command_factory = command_factory
        self.guard, self.progress = guard, progress
        self.module_root = module_root or root / 'upstream/holodori/upstream'
        self.process = None

    @staticmethod
    def read_status(path, job_id):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            return record if record.get('job_id') == job_id else None
        except (OSError, ValueError, AttributeError):
            return None

    def run(self, config: TaskConfig, *, timeout: float = 900,
            stop_grace: float = 10) -> WorkerOutcome:
        if self.process is not None:
            raise WorkerError('Another module still owns the game')
        if config.task not in ('fishing', 'jump_rope', 'cooking', 'high_low'):
            raise WorkerError('Live cannot be delegated to a minigame worker')
        if self.stopping(): raise Cancelled('已停止')
        self.guard()
        job_id = uuid.uuid4().hex
        directory = self.sessions / job_id
        directory.mkdir(parents=True, exist_ok=False)
        config_path, status_path, stop_path = (directory / name for name in
                                             ('config.json', 'status.json', 'stop'))
        config_path.write_text(json.dumps(config.to_dict()), encoding='utf-8')
        command = ([str(self.python), str(self.root / 'agent/module_worker.py'),
                    '--config', str(config_path), '--status', str(status_path),
                    '--stop', str(stop_path), '--job-id', job_id,
                    '--parent-pid', str(os.getpid()), '--module-root', str(self.module_root)]
                   if self.command_factory is None else
                   self.command_factory(config_path, status_path, stop_path, job_id))
        timed_out = cancelled = False
        forced = False
        guard_error = last_status = None
        started = time.monotonic()
        try:
            with (directory / 'worker.log').open('w', encoding='utf-8') as log:
                self.process = subprocess.Popen(command, cwd=self.root, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    env={**os.environ, 'PYTHONUTF8': '1'})
                stopping_at = None
                while self.process.poll() is None:
                    if guard_error is None:
                        try:
                            self.guard()
                        except Exception as error:
                            guard_error = error
                    status = self.read_status(status_path, job_id)
                    if status is not None and status != last_status:
                        self.progress(status)
                        last_status = status
                    if self.stopping(): cancelled = True
                    if time.monotonic() - started >= timeout: timed_out = True
                    if cancelled or timed_out or guard_error is not None:
                        stop_path.touch(exist_ok=True)
                        if stopping_at is None: stopping_at = time.monotonic()
                        if time.monotonic() - stopping_at >= stop_grace:
                            self.process.terminate()
                            self.process.wait(timeout=5)
                            forced = True
                            break
                    time.sleep(.05)
                exit_code = self.process.returncode
            if cancelled: raise Cancelled('已停止小遊戲')
            if guard_error is not None: raise guard_error
            if timed_out: raise WorkerError('小遊戲執行逾時，已保存診斷')
            record = self.read_status(status_path, job_id)
            if exit_code != 0 or not record or record.get('state') != 'complete':
                raise WorkerError((record or {}).get('error', '小遊戲未回報完成證據'))
            completed = record.get('completed')
            if type(completed) is not int or completed < 1:
                raise WorkerError('小遊戲完成數量無效')
            self.guard()
            self.progress(record)
            return WorkerOutcome(completed, record)
        finally:
            # Unexpected errors must not leave an upstream process running.
            if self.process is not None and self.process.poll() is None:
                stop_path.touch(exist_ok=True)
                try:
                    self.process.wait(timeout=stop_grace)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                    forced = True
            # The caller releases the known module input domain after a forced
            # termination, before any other executor may obtain ownership.
            try:
                if forced:
                    self.emergency_release()
            finally:
                self.process = None
