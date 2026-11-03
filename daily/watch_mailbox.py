"""Filesystem work stays off the watchdog's deadline/termination thread."""
import json
from pathlib import Path
import threading


def read_message(path, job_id):
    try:
        with path.open('r', encoding='utf-8') as stream:
            text = stream.read(8193)
        if len(text) > 8192:
            return None
        value = json.loads(text)
        return value if isinstance(value, dict) and value.get('job_id') == job_id else None
    except (OSError, ValueError):
        return None


class WatchMailbox:
    """Memory-only message(), flag(), publish() for the deadline loop.

    Reader and writer are separate daemon threads. If either blocks forever on
    disk, the deadline loop still runs from its last immutable snapshot.
    """
    def __init__(self, directory: Path, job_id: str, writer, *, reader=read_message):
        self.directory, self.job_id = directory, job_id
        self.writer, self.reader = writer, reader
        self._snapshot = {}
        self._pending = None
        self._sequence = self._written = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.error = None
        self._threads = []

    def start(self):
        if self._threads:
            raise RuntimeError('Watchdog mailbox already started')
        for method in (self._read, self._write):
            thread = threading.Thread(target=method, daemon=True)
            self._threads.append(thread)
            thread.start()

    def message(self, name):
        return self._snapshot.get(name)

    def flag(self, name):
        return self._snapshot.get(name, False) is True

    def publish(self, state, **details):
        if not self._threads:
            raise RuntimeError('Watchdog mailbox is not running')
        self._sequence += 1
        self._pending = (self._sequence, state, details)
        self._wake.set()

    def _read(self):
        try:
            while not self._stop.is_set():
                snapshot = {name: self.reader(self.directory/(name+'.json'), self.job_id)
                            for name in ('arm', 'observation', 'click')}
                snapshot.update({name: (self.directory/name).exists()
                                 for name in ('abort', 'cancel_before_arm')})
                self._snapshot = snapshot
                self._stop.wait(.02)
        except Exception as error:
            self.error = f'reader: {type(error).__name__}: {error}'[:500]

    def _write(self):
        try:
            while True:
                item = self._pending
                if item is not None and item[0] != self._written:
                    self.writer(item[1], **item[2])
                    self._written = item[0]
                if self._stop.is_set() and self._written == self._sequence:
                    return
                self._wake.wait(.02)
                self._wake.clear()
        except Exception as error:
            self.error = f'writer: {type(error).__name__}: {error}'[:500]

    def finish(self, timeout=1):
        # Only call after the game is closed or final Auto is verified. A
        # bounded join is cleanup, never part of a live deadline decision.
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout/max(1, len(self._threads)))
