"""Disposable local-controller process for guarded multiplayer UI clicks.

Dispatch/ack access is memory-only. Pipe I/O runs on separate threads. On an
emergency the caller closes the game first, then aborts this lane, waits for
the owned input process to exit, and releases its known managed inputs.
Not connected to the currently disabled multiplayer task.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from .game_process import current_creation_time
from .guarded_click import ClickRequest, GuardedClickError
from .input_release import ManagedInputRelease
from .processes import publish_status


class InputLane:
    def __init__(self, root: Path, python: Path, directory: Path, identity, *,
                 launcher=subprocess.Popen, creation_time=current_creation_time,
                 release_factory=ManagedInputRelease, clock=time.monotonic, pause=time.sleep,
                 exclusive_input=False):
        self.root, self.python, self.directory = root, python, directory
        self.identity = dict(identity)
        self.launcher, self.creation_time = launcher, creation_time
        self.release_factory, self.clock, self.pause = release_factory, clock, pause
        self.exclusive_input = exclusive_input is True
        self.process = self.log = None
        self.error = None
        self.ack = None
        self.ready = None
        self._sequence = self._active = 0
        self._dirty = False
        self._request = None
        self._ready = False
        self._stopped = threading.Event()
        self._wake = threading.Event()
        self._threads = []
        self._state_lock = threading.Lock()  # Memory only; never held across pipe/file/process I/O.
        self._owner = threading.get_ident()
        self.cleanup_complete = False

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise GuardedClickError('Input lane ownership changed')

    def prepare(self):
        self._check_owner()
        if self.process is not None or self._stopped.is_set():
            raise GuardedClickError('Input lane cannot be reused')
        if not self.exclusive_input:
            raise GuardedClickError('The round must reserve Right Alt and left mouse exclusively')
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
            config = dict(self.identity, parent_pid=os.getpid(), parent_creation=self.creation_time())
            config['exclusive_input'] = True
            path = self.directory/'config.json'
            publish_status(path, config)
            self.log = (self.directory/'worker.log').open('w', encoding='utf-8')
            self.process = self.launcher([str(self.python), '-I', '-B', '-X', 'utf8',
                str(self.root/'agent/guarded_click_worker.py'), '--config', str(path)],
                cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                text=True, encoding='utf-8', bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for method in (self._read, self._write):
                thread = threading.Thread(target=method, daemon=True)
                self._threads.append(thread)
                thread.start()
            deadline = self.clock()+12
            while not self._ready:
                if self.error or self.process.poll() is not None or self.clock() >= deadline:
                    raise GuardedClickError(self.error or 'Input worker did not become ready')
                self.pause(.02)
            if self.process.poll() is not None:
                raise GuardedClickError('Input worker exited during readiness handshake')
            return self
        except BaseException:
            self.abort()
            raise

    def dispatch(self, message):
        self._check_owner()
        if (not self._ready or self._stopped.is_set() or self.error
                or self.process.poll() is not None or self._active):
            raise GuardedClickError('Input lane is not ready for another click')
        request = ClickRequest.parse(message, self.identity['job_id'],
            last_sequence=self._sequence, now=self.clock())
        with self._state_lock:
            # Completion reader and dispatch must change ownership atomically:
            # an old ACK cannot clear the dirty flag of a newer request.
            if self._active or self._stopped.is_set() or self.error:
                raise GuardedClickError('Input lane ownership changed before dispatch')
            self._sequence = self._active = request.sequence
            self._dirty = True
            self.ack = None
            self._request = asdict(request)
        self._wake.set()
        return request.sequence

    def _read(self):
        try:
            while not self._stopped.is_set():
                line = self.process.stdout.readline(4097)
                if not line:
                    raise GuardedClickError('Input worker pipe closed')
                if len(line) > 4096 or not line.endswith('\n'):
                    raise GuardedClickError('Input worker response exceeds limit')
                if not line.startswith('DAILY_INPUT:'):
                    continue  # Native library console messages are not ACKs.
                data = json.loads(line.removeprefix('DAILY_INPUT:'))
                if not isinstance(data, dict) or data.get('job_id') != self.identity['job_id']:
                    raise GuardedClickError('Input response belongs to another round')
                state = data.get('state')
                if state == 'ready' and not self._ready and not self._sequence:
                    if any(data.get(key) != self.identity[key] for key in ('game_pid', 'game_image')):
                        raise GuardedClickError('Input worker bound another game')
                    self.ready = dict(data)
                    self._ready = True
                elif state == 'complete':
                    with self._state_lock:
                        if self._stopped.is_set():
                            continue  # Abort owns uncertainty until cleanup succeeds.
                        if (type(data.get('sequence')) is not int or not self._active
                                or data['sequence'] != self._active or data.get('released') is not True):
                            raise GuardedClickError('Unexpected input completion')
                        self._active = 0
                        self._dirty = False
                        self.ack = dict(data)
                else:
                    raise GuardedClickError(data.get('error') or 'Unexpected input response')
        except Exception as error:
            if not self._stopped.is_set():
                self.error = f'reader: {type(error).__name__}: {error}'[:500]

    def _write(self):
        try:
            while not self._stopped.is_set():
                with self._state_lock:
                    request = self._request
                    self._request = None
                if request is not None:
                    self.process.stdin.write(json.dumps(request)+'\n')
                    self.process.stdin.flush()
                self._wake.wait(.02)
                self._wake.clear()
        except Exception as error:
            if not self._stopped.is_set():
                self.error = f'writer: {type(error).__name__}: {error}'[:500]

    def abort(self):
        """No pipe/file operation may precede terminating a pending worker."""
        self._check_owner()
        with self._state_lock:
            self._stopped.set()
            dirty = self._dirty
        self._wake.set()
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=3)
            if self.process.poll() is None:
                raise GuardedClickError('Input process is still running; cleanup cannot proceed')
            if dirty:
                release = self.release_factory()
                for attempt in range(3):
                    try:
                        release.release_after_exit(self.process, exclusive_input=self.exclusive_input)
                        break
                    except GuardedClickError:
                        if attempt == 2:
                            raise  # Keep dirty and stopped: never allow another round.
                        self.pause(.02)
                self._dirty = False
            for thread in self._threads:
                thread.join(.1)
            # Only after process exit and managed input cleanup. An I/O thread
            # stuck in a fake/test pipe must not make emergency cleanup wait.
            if not any(thread.is_alive() for thread in self._threads):
                self.process.stdin.close()
                self.process.stdout.close()
        if self.log is not None:
            self.log.close()
        self.cleanup_complete = True
