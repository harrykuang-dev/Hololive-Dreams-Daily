import json
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from daily.guarded_click import GuardedClickError
from daily.input_lane import InputLane


def harness(tmp_path, *, block_write=False, wrong_identity=False):
    events, messages = [], queue.Queue()
    entered, resume = threading.Event(), threading.Event()
    class Process:
        returncode = None
        def __init__(self):
            self.stdin = SimpleNamespace(write=self.write, flush=lambda: None, close=lambda: None)
            self.stdout = SimpleNamespace(readline=lambda limit: messages.get(timeout=5), close=lambda: None)
        def write(self, line):
            entered.set()
            if block_write:
                resume.wait(5)
            if self.returncode is not None:
                raise BrokenPipeError('owned process exited')
            command = json.loads(line)
            messages.put('DAILY_INPUT:'+json.dumps({'state': 'complete', 'job_id': 'one',
                'sequence': command['sequence'], 'released': True})+'\n')
        def poll(self): return self.returncode
        def terminate(self):
            events.append('terminate_owned_input')
            self.returncode = 1
            messages.put('')
        def wait(self, timeout):
            assert self.returncode is not None
            events.append('confirmed_input_exit')
            return self.returncode
    process = Process()
    identity = {'job_id': 'one', 'game_pid': 123, 'game_image': 'game', 'hwnd': 42}
    def launch(command, **kwargs):
        assert kwargs['stdin'] is not None and kwargs['stdout'] is not None
        ready = dict(identity, state='ready')
        if wrong_identity: ready['game_pid'] = 999
        messages.put('DAILY_INPUT:'+json.dumps(ready)+'\n')
        return process
    class Release:
        def release_after_exit(self, child, *, exclusive_input):
            assert exclusive_input is True
            assert child is process and child.poll() is not None
            events.append('release_managed_inputs')
    lane = InputLane(tmp_path, tmp_path/'python.exe', tmp_path/'input', identity,
        launcher=launch, creation_time=lambda: 12345, release_factory=Release, clock=lambda: 100,
        exclusive_input=True)
    return lane, process, events, entered, resume


def test_unarmed_input_preflight_closes_without_sending_cleanup_keys(tmp_path):
    lane, _, events, _, _ = harness(tmp_path)
    lane.prepare()
    lane.abort()
    assert events == ['terminate_owned_input', 'confirmed_input_exit']


def test_wrong_game_identity_never_accepts_clicks_or_releases_unowned_keys(tmp_path):
    lane, _, events, _, _ = harness(tmp_path, wrong_identity=True)
    with pytest.raises(GuardedClickError, match='another game'):
        lane.prepare()
    assert events == ['terminate_owned_input', 'confirmed_input_exit']


def test_blocked_pipe_cannot_delay_terminating_input_and_releasing_owned_keys(tmp_path):
    lane, _, events, entered, resume = harness(tmp_path, block_write=True)
    lane.prepare()
    lane.dispatch(dict(job_id='one', sequence=1, x=100, y=200, captured_at=100))
    try:
        assert entered.wait(1) and not resume.is_set()
        started = time.monotonic()
        lane.abort()
        assert time.monotonic()-started < 1
        assert not resume.is_set()  # Pipe writer is STILL blocked.
        assert events == ['terminate_owned_input', 'confirmed_input_exit', 'release_managed_inputs']
    finally:
        resume.set()
        for thread in lane._threads: thread.join(1)


def test_completed_click_ack_releases_ownership_and_old_commands_cannot_repeat(tmp_path):
    lane, _, events, entered, _ = harness(tmp_path)
    lane.prepare()
    command = dict(job_id='one', sequence=1, x=100, y=200, captured_at=100)
    try:
        lane.dispatch(command)
        assert entered.wait(1)
        deadline = time.monotonic()+1
        while lane.ack is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert lane.ack['sequence'] == 1 and lane.ack['released']
        with pytest.raises(GuardedClickError, match='sequence'):
            lane.dispatch(command)
        lane.dispatch(dict(command, sequence=2))
        assert lane.ack is None or lane.ack['sequence'] == 2
        deadline = time.monotonic()+1
        while lane.ack is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert lane.ack['sequence'] == 2
    finally:
        lane.abort()
    assert 'release_managed_inputs' not in events  # Already ACKed as released.


def test_missing_reservation_blocks_before_process_creation(tmp_path):
    lane, _, events, _, _ = harness(tmp_path)
    lane.exclusive_input = False
    with pytest.raises(GuardedClickError, match='reserve'):
        lane.prepare()
    assert lane.process is None and not events


def test_ack_waiting_for_lock_cannot_erase_failed_abort_cleanup(tmp_path):
    lane, _, events, _, _ = harness(tmp_path)
    lane.prepare()
    waiting, resume = threading.Event(), threading.Event()
    lock = threading.Lock()
    class BarrierLock:
        def __enter__(self):
            if threading.current_thread() is lane._threads[0] and not waiting.is_set():
                waiting.set()
                assert resume.wait(3)
            lock.acquire()
        def __exit__(self, *args): lock.release()
    lane._state_lock = BarrierLock()
    attempts = []
    class FailingRelease:
        def release_after_exit(self, child, **kwargs):
            attempts.append(child)
            raise GuardedClickError('partial release')
    lane.release_factory = FailingRelease
    lane.dispatch(dict(job_id='one', sequence=1, x=100, y=200, captured_at=100))
    try:
        assert waiting.wait(1)
        with pytest.raises(GuardedClickError, match='partial'):
            lane.abort()
        assert len(attempts) == 3 and lane._dirty and not lane.cleanup_complete
        resume.set()
        for thread in lane._threads: thread.join(1)
        assert lane._dirty  # The ACK read before abort must not clear this.
        with pytest.raises(GuardedClickError, match='partial'):
            lane.abort()
        assert len(attempts) == 6 and not lane.cleanup_complete
        with pytest.raises(GuardedClickError):
            lane.dispatch(dict(job_id='one', sequence=2, x=100, y=200, captured_at=100))
    finally:
        resume.set()
        lane.release_factory = lambda: SimpleNamespace(release_after_exit=lambda *a, **kw: None)
        lane.abort()
    assert lane.cleanup_complete and not lane._dirty


def test_worker_exit_timeout_does_not_release_while_it_could_still_post(tmp_path):
    import subprocess
    lane, process, events, entered, resume = harness(tmp_path, block_write=True)
    lane.prepare()
    lane.dispatch(dict(job_id='one', sequence=1, x=100, y=200, captured_at=100))
    original = process.terminate
    process.terminate = lambda: events.append('terminate_pending')
    def timeout(timeout): raise subprocess.TimeoutExpired('fixture', timeout)
    process.wait = timeout
    try:
        assert entered.wait(1)
        with pytest.raises(subprocess.TimeoutExpired): lane.abort()
        assert lane._dirty and not lane.cleanup_complete
        assert 'release_managed_inputs' not in events
    finally:
        original()
        resume.set()
        lane.abort()
    assert lane.cleanup_complete and events[-1] == 'release_managed_inputs'
