import json
from pathlib import Path

from agent.recording_worker import publish_status


def test_windows_status_replacement_retries_a_brief_reader_lock(tmp_path, monkeypatch):
    path = tmp_path / 'status.json'
    path.write_text('{"state":"ready"}')
    original = Path.replace
    attempts = []

    def replace(self, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError('reader holds file')
        return original(self, target)

    monkeypatch.setattr(Path, 'replace', replace)
    publish_status(path, {'state': 'complete', 'frames': 5})
    assert len(attempts) == 3
    assert json.loads(path.read_text()) == {'state': 'complete', 'frames': 5}
    assert not list(tmp_path.glob('*.pending'))
