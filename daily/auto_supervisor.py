"""One-round independent Auto watchdog handshake, before multiplayer input.

The multiplayer runner remains disabled until native integration is accepted.
"""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .auto_deadline import CountdownObservation, finite_number
from .auto_recovery import WatchdogRecovery
from .game_process import GameProcess, current_creation_time
from .guarded_click import ClickRequest
from .inputs import Cancelled
from .processes import publish_status
from .live_validation import LiveStartProof, validate_live_start


class AutoGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartPermit:
    job_id: str
    nonce: str
    proof: LiveStartProof


class AutoSupervisor:
    def __init__(self, root: Path, python: Path, sessions: Path, hwnd: int, *,
                 guard=lambda: None, stopping=lambda: False, trace=lambda *args, **kwargs: None,
                 game_factory=GameProcess, launcher=subprocess.Popen,
                 recovery_factory=WatchdogRecovery,
                 creation_time=current_creation_time, clock=time.monotonic, pause=time.sleep):
        self.root, self.python, self.sessions, self.hwnd = root, python, sessions, hwnd
        self.guard, self.stopping, self.trace = guard, stopping, trace
        self.game_factory, self.launcher, self.creation_time = game_factory, launcher, creation_time
        self.recovery_factory = recovery_factory
        self.clock, self.pause = clock, pause
        self.process = self.game = self.log = self.recovery = None
        self.directory = None
        self.phase = 'new'
        self.job_id = uuid.uuid4().hex
        self._armed = False
        self._input_sequence = 0
        self._permit = None
        self._owner = threading.get_ident()
        self._lock = threading.RLock()

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise AutoGuardError('Multiplayer input ownership changed')
        if self.stopping():
            raise Cancelled('已停止多人流程')
        self.guard()

    def _status(self):
        try:
            value = json.loads((self.directory/'status.json').read_text(encoding='utf-8'))
            if value.get('job_id') != self.job_id:
                return None
            updated = value.get('updated_monotonic')
            if not finite_number(updated) or not 0 <= self.clock()-updated <= 2:
                return None
            return value
        except (OSError, ValueError, AttributeError):
            return None

    def _wait(self, expected, timeout, *, accept=lambda _state: True):
        deadline = self.clock()+timeout
        while True:
            self._check_owner()
            state = self._status()
            if state is not None:
                name = state.get('state')
                if name in {'failed', 'game_closed', 'closing_game', 'cancelled_before_arm'}:
                    raise AutoGuardError(state.get('error') or state.get('reason') or name)
                if name in expected and accept(state):
                    code = self.process.poll()
                    if code is not None and not (name == 'auto_verified' and code == 0):
                        raise AutoGuardError('Watchdog exited during handshake')
                    return state
            code = self.process.poll()
            if code is not None:
                # The worker can publish its final ACK and exit between our
                # status read and poll. Re-read once before rejecting it.
                final = self._status()
                if (code == 0 and final is not None and final.get('state') == 'auto_verified'
                        and 'auto_verified' in expected and accept(final)):
                    return final
                raise AutoGuardError('Watchdog did not acknowledge the current round')
            if self.clock() >= deadline:
                raise AutoGuardError('Watchdog handshake timed out')
            self.pause(.02)

    def prepare(self):
        with self._lock:
            self._check_owner()
            if self.phase != 'new':
                raise AutoGuardError('A watchdog cannot be reused for another round')
            try:
                self.game = self.game_factory(self.hwnd)
                self.directory = self.sessions/self.job_id
                self.directory.mkdir(parents=True, exist_ok=False)
                config = {'job_id': self.job_id, 'parent_pid': os.getpid(),
                          'parent_creation': self.creation_time(), 'hwnd': self.hwnd,
                          'game_pid': self.game.pid, 'game_image': self.game.image,
                          'input_protocol': 1}
                path = self.directory/'config.json'
                publish_status(path, config)
                self.log = (self.directory/'watchdog.log').open('w', encoding='utf-8')
                # Failure to break away from the caller's job is fatal BEFORE
                # matching. A parent/turn teardown must not kill this watchdog.
                flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                         | 0x01000000 | 0x00008000)  # BREAKAWAY_FROM_JOB | ABOVE_NORMAL_PRIORITY
                self.process = self.launcher([str(self.python), '-I', '-B', '-X', 'utf8',
                    str(self.root/'agent/auto_watch_worker.py'), '--config', str(path)],
                    cwd=self.root, stdout=self.log, stderr=self.log, creationflags=flags)
                ready = self._wait({'ready'}, 18)
                if ready.get('game_pid') != self.game.pid or ready.get('game_image') != self.game.image:
                    raise AutoGuardError('Watchdog bound a different game process')
                if ready.get('input_protocol') != 1:
                    raise AutoGuardError('Watchdog lacks the guarded input worker')
                self.recovery = self.recovery_factory(self.process, self.hwnd, game_pid=self.game.pid,
                    game_image=self.game.image, worker_pid=ready.get('input_worker_pid'),
                    worker_creation=ready.get('input_worker_creation'))
                self.phase = 'prepared'
                self.trace('multi_watchdog_ready', job_id=self.job_id, game_pid=self.game.pid)
            except BaseException:
                self.close()
                raise
        return self

    def match(self, x, y, *, captured_at):
        with self._lock:
            self._check_owner()
            if self.phase != 'prepared':
                raise AutoGuardError('Matching requires a fresh prepared watchdog')
            try:
                self._armed = True  # Treat even an uncertain arm-file write as armed.
                self.recovery.armed.set()
                publish_status(self.directory/'arm.json', {'job_id': self.job_id,
                    'decision': 'arm', 'captured_at': self.clock()})
                status = self._wait({'armed'}, 3)
                deadline = status.get('deadline')
                if not finite_number(deadline) or deadline-self.clock() < 5:
                    raise AutoGuardError('Insufficient guarded time before matching')
                self._check_owner()
                if (self.game.backend.pid_for_window(self.hwnd) != self.game.pid
                        or not self.game.backend.is_running(self.game.handle)
                        or self.process.poll() is not None):
                    raise AutoGuardError('Game/watchdog identity changed before matching')
                self.phase = 'matching'
                self.trace('multi_matching_guarded', job_id=self.job_id, deadline=deadline)
                # Trace/recording hooks may block. Nothing may enter matching
                # on an acknowledgement obtained before those hooks return.
                self.check()
                if (self.game.backend.pid_for_window(self.hwnd) != self.game.pid
                        or not self.game.backend.is_running(self.game.handle)):
                    raise AutoGuardError('Game identity changed after matching audit')
                self._click(x, y, captured_at, 'match')
            except BaseException:
                self.close()
                raise

    def click(self, x, y, *, captured_at):
        with self._lock:
            try:
                self._permit = None
                self._click(x, y, captured_at, 'prepare')
            except BaseException:
                self.close()
                raise

    def _click(self, x, y, captured_at, purpose, *, before_post=None):
        self._check()
        sequence = self._input_sequence + 1
        request = ClickRequest.parse({'job_id': self.job_id, 'sequence': sequence,
            'x': x, 'y': y, 'captured_at': captured_at}, self.job_id,
            last_sequence=self._input_sequence, now=self.clock())
        request.check_live_purpose(purpose)
        self.trace('multi_click_request', purpose=purpose, sequence=sequence, x=x, y=y,
                   captured_at=captured_at)
        self._check()
        if before_post is not None:
            before_post()
        request.check_fresh(self.clock())
        self.recovery.input_possible.set()
        publish_status(self.directory/'click.json', {'job_id': self.job_id,
            'purpose': purpose, 'request': asdict(request)})
        self._wait({'armed'}, 2, accept=lambda state: type(state.get('input_ack')) is int
                   and state['input_ack'] == sequence)
        request.check_fresh(self.clock())
        self._input_sequence = sequence
        return sequence

    def observe(self, observation: CountdownObservation):
        with self._lock:
            try:
                self.check()
                if observation.start_authorized:
                    raise AutoGuardError('Preparation cannot authorize a Live start')
                self._permit = None
                publish_status(self.directory/'observation.json', {
                    'job_id': self.job_id, 'observation': asdict(observation)})
            except BaseException:
                self.close()
                raise

    def check(self):
        with self._lock:
            return self._check()

    def _check(self):
        self._check_owner()
        if self.phase != 'matching':
            raise AutoGuardError('No active matching watchdog')
        status = self._status()
        if (status is None or status.get('state') != 'armed' or self.process.poll() is not None
                or not finite_number(status.get('deadline')) or self.clock() >= status['deadline']):
            raise AutoGuardError('Independent watchdog is not healthy')
        if (self.game.backend.pid_for_window(self.hwnd) != self.game.pid
                or not self.game.backend.is_running(self.game.handle)):
            raise AutoGuardError('Controlled game identity changed')

    def prepare_start(self, config, state, budget_remaining=None, *, energy=None):
        """Mint one round-bound permit only after the full same-frame gate."""
        with self._lock:
            try:
                self.check()
                if config.task != 'multi_live':
                    raise AutoGuardError('Only multiplayer can use this guard')
                proof = validate_live_start(config, state, budget_remaining, energy=energy, now=self.clock())
                self._permit = StartPermit(self.job_id, uuid.uuid4().hex, proof)
                return self._permit
            except BaseException:
                self.close()
                raise

    def verify_permit(self, permit):
        with self._lock:
            return self._verify_permit(permit)

    def _verify_permit(self, permit):
        self.check()
        if (not isinstance(permit, StartPermit) or self._permit is not permit
                or permit.job_id != self.job_id):
            raise AutoGuardError('Missing, replaced or replayed final Live gate permit')
        proof = permit.proof
        checked = validate_live_start(proof.config, proof.state, proof.budget_remaining,
                                      energy=proof.energy, now=self.clock())
        if checked != proof:
            raise AutoGuardError('The final Live gate proof changed')

    def perform_start(self, permit, x, y):
        """Own the order: full proof, pre-post check, input ACK, final release."""
        with self._lock:
            try:
                self.verify_permit(permit)
                auto = permit.proof.state.auto
                observed = CountdownObservation(auto.scene, auto.captured_at, auto.enabled, auto.remaining)
                publish_status(self.directory/'observation.json', {
                    'job_id': self.job_id, 'observation': asdict(observed)})
                self._wait({'armed'}, .75, accept=lambda state: state.get('ready_seen') is True
                           and state.get('last_capture') == auto.captured_at)
                sequence = self._click(x, y, auto.captured_at, 'start',
                                       before_post=lambda: self.verify_permit(permit))
                self.verify_permit(permit)
                self._permit = None
                message = CountdownObservation(auto.scene, auto.captured_at, auto.enabled,
                                                auto.remaining, start_authorized=True)
                publish_status(self.directory/'observation.json', {
                    'job_id': self.job_id, 'input_sequence': sequence, 'observation': asdict(message)})
                self._wait({'auto_verified'}, .75, accept=lambda state: state.get('input_ack') == sequence)
                self.phase = 'safe_auto'
            except BaseException:
                self.close()
                raise

    def close(self):
        with self._lock:
            if self.phase == 'closed':
                return
            try:
                if self._armed and self.phase != 'safe_auto' and self.game is not None:
                    # Kill the exact retained game handle BEFORE any file/log
                    # write can block the parent cancellation path.
                    self.game.emergency_stop()
                if self.process is not None and self.process.poll() is None:
                    if not self._armed:
                        (self.directory/'cancel_before_arm').touch(exist_ok=True)
                    # The watchdog owns the input worker. Let it observe the
                    # closed game and clean up before removing any handles.
                    self.process.wait(timeout=10)
                if self.recovery is not None:
                    self.recovery.finish()
            finally:
                if self.game is not None:
                    self.game.close()
                if self.log is not None:
                    self.log.close()
                self.phase = 'closed'
