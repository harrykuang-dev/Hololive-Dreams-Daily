from datetime import datetime, timedelta, timezone
import json

import pytest

from daily.worker_start import authorize_start, wait_for_start, StartAuthorizationError


def test_start_token_is_bound_to_job_and_freshness(tmp_path):
    path = tmp_path / 'start.json'
    authorize_start(path, 'current', {'cost': 0})
    record = wait_for_start(path, 'current', lambda: False)
    assert record['metadata']['cost'] == 0
    with pytest.raises(StartAuthorizationError):
        wait_for_start(path, 'other', lambda: False)
    stale = datetime.fromisoformat(record['authorized_at'])+timedelta(seconds=4)
    with pytest.raises(StartAuthorizationError):
        wait_for_start(path, 'current', lambda: False, clock=lambda: stale)


def test_cancelled_worker_never_accepts_even_a_valid_start_token(tmp_path):
    path = tmp_path / 'start.json'
    authorize_start(path, 'current')
    with pytest.raises(InterruptedError):
        wait_for_start(path, 'current', lambda: True)


def test_missing_or_invalid_authorization_cannot_start_worker(tmp_path):
    path = tmp_path / 'start.json'
    with pytest.raises(StartAuthorizationError):
        wait_for_start(path, 'current', lambda: False, timeout=0)
    path.write_text(json.dumps({'job_id': 'current', 'decision': 'start', 'authorized_at': 'invalid'}))
    with pytest.raises(StartAuthorizationError):
        wait_for_start(path, 'current', lambda: False)
