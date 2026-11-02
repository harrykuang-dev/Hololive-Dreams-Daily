import json
import threading
import time
from types import SimpleNamespace

import pytest

from daily.auto_deadline import AutoDeadline
from daily.auto_watch import watch
from daily.watch_mailbox import WatchMailbox, read_message


@pytest.mark.parametrize('blocked', ['reader', 'writer'])
def test_disk_stall_does_not_block_deadline_game_shutdown(tmp_path, monkeypatch, blocked):
    # Shorten only the TEST timer; no actual game/process backend is used.
    monkeypatch.setattr(AutoDeadline, 'WINDOW', .4)
    release = threading.Event()
    stuck = threading.Event()
    armed = threading.Event()
    stopped = threading.Event()
    job = 'blocked-io-round'
    def reader(path, job_id):
        if path.name == 'observation.json' and armed.is_set() and blocked == 'reader':
            stuck.set()
            release.wait(5)
        return read_message(path, job_id)
    def writer(state, **details):
        if state == 'ready':
            (tmp_path/'arm.json').write_text(json.dumps({'job_id': job, 'decision': 'arm',
                                                        'captured_at': time.monotonic()}))
        if state == 'armed':
            armed.set()
            if blocked == 'writer':
                stuck.set()
                release.wait(5)
    channel = WatchMailbox(tmp_path, job, writer, reader=reader)
    channel.start()
    game = SimpleNamespace(pid=2, image='game', close=lambda: None,
        emergency_stop=lambda: stopped.set() or True)
    start = time.monotonic()
    try:
        code = watch({'job_id': job, 'parent_pid': 1, 'game_pid': 2,
                      'game_image': 'game', 'hwnd': 3}, channel,
                     game_factory=lambda *args, **kwargs: game, alive=lambda pid: True)
        assert stuck.is_set() and not release.is_set()
        assert code == 2 and stopped.is_set()
        assert time.monotonic()-start < 2  # Stop occurred while disk was STILL stuck.
    finally:
        release.set()
        channel.finish()


def test_oversized_or_wrong_job_message_is_not_accepted(tmp_path):
    path = tmp_path/'message.json'
    path.write_text(json.dumps({'job_id': 'old-job'}))
    assert read_message(path, 'new-job') is None
    path.write_text(json.dumps({'job_id': 'new-job', 'data': 'x'*9000}))
    assert read_message(path, 'new-job') is None
