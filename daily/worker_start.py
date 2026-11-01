"""Single-job, short-lived authorization after a minigame worker is ready."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .processes import publish_status


class StartAuthorizationError(RuntimeError):
    pass


def authorize_start(path: Path, job_id: str, metadata=None):
    publish_status(path, {'job_id': job_id, 'decision': 'start',
                          'authorized_at': datetime.now(timezone.utc).isoformat(),
                          'metadata': metadata or {}})


def wait_for_start(path: Path, job_id: str, stopped, *, timeout=30,
                   clock=lambda: datetime.now(timezone.utc)):
    deadline = time.monotonic()+timeout
    while True:
        if stopped():
            raise InterruptedError('Worker start cancelled')
        try:
            decision = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            decision = None
        if decision is not None:
            try:
                age = (clock()-datetime.fromisoformat(decision['authorized_at'])).total_seconds()
                valid = (decision['job_id'] == job_id and decision['decision'] == 'start'
                         and -.5 <= age <= 3)
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise StartAuthorizationError('Wrong job, invalid or expired start authorization')
            if stopped():
                raise InterruptedError('Worker start cancelled')
            return decision
        if time.monotonic() >= deadline:
            raise StartAuthorizationError('No start authorization before timeout')
        time.sleep(.02)
