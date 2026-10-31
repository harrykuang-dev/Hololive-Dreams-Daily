"""Durable results; old complete records are evidence, not permission to skip."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from .contracts import TaskConfig, TaskResult, TaskStatus


class Journal:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def record(self, config: TaskConfig, result: TaskResult, *, session: str) -> Path:
        record = {'schema_version': 1, 'session': session,
                  'recorded_at': datetime.now(timezone.utc).isoformat(),
                  'config': config.to_dict(), 'result': result.to_dict()}
        destination = self.root / f'{session}-{config.task}.json'
        temporary = destination.with_suffix(f'.{uuid.uuid4().hex}.pending')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
        return destination

    def previous(self, session: str, task: str) -> dict | None:
        try:
            data = json.loads((self.root / f'{session}-{task}.json').read_text(encoding='utf-8'))
            if data['schema_version'] != 1 or data['session'] != session:
                return None
            return data
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def verified_complete(record: dict | None) -> bool:
        # Entry-only, skipped and interrupted runs are never complete runs.
        return bool(record and record.get('result', {}).get('status') == TaskStatus.COMPLETE
                    and not record.get('result', {}).get('pending_round', False)
                    and record.get('result', {}).get('evidence'))

