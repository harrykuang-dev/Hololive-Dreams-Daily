"""Game-day attempt records: reset at04:00 UTC+8, recorded tests after04:05."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import uuid


GAME_TIMEZONE = timezone(timedelta(hours=8))


class DailyUnavailable(RuntimeError):
    pass


def game_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError('Game-day timestamps require a timezone')
    return (now.astimezone(GAME_TIMEZONE) - timedelta(hours=4)).date().isoformat()


def test_window_start(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError('Test-window timestamps require a timezone')
    return now.astimezone(GAME_TIMEZONE).replace(hour=4, minute=5, second=0, microsecond=0)


@dataclass
class DailyAttempt:
    path: Path
    record: dict

    def finish(self, *, evidence=None, error=None):
        updated = {**self.record, 'state': 'failed' if error else 'complete',
                   'finished_at': datetime.now(timezone.utc).isoformat(),
                   'evidence': evidence, 'error': str(error) if error else None}
        temporary = self.path.with_suffix(f'.{uuid.uuid4().hex}.pending')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        self.record = updated


class DailyAttempts:
    def __init__(self, directory: Path, *, not_before=None, guard=lambda: None,
                 recording=lambda: None, clock=lambda: datetime.now(timezone.utc)):
        self.directory, self.guard, self.recording, self.clock = directory, guard, recording, clock
        self.not_before = datetime.fromisoformat(not_before) if isinstance(not_before, str) else not_before
        if self.not_before is not None and self.not_before.tzinfo is None:
            raise ValueError('Daily test restriction requires a timezone')

    @classmethod
    def from_runtime(cls, root: Path, data: Path, recording):
        path = root / 'config' / 'recording.json'
        settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        return cls(data / 'attempts', not_before=settings.get('daily_test_not_before'),
                   guard=recording.check, recording=recording.paths)

    def reserve(self, task: str) -> DailyAttempt:
        if not re.fullmatch('[a-z][a-z0-9_-]{0,79}', task):
            raise ValueError('Invalid daily task identifier')
        self.guard()
        now = self.clock()
        day = game_day(now)
        if self.not_before is not None:
            opens = max(self.not_before, test_window_start(now))
            if now < opens:
                raise DailyUnavailable(f'每日限次實測等待 {opens.astimezone(GAME_TIMEZONE):%Y-%m-%d %H:%M} UTC+8')
        directory = self.directory / day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{task}.json'
        record = {'schema_version': 1, 'task': task, 'game_day': day,
                  'state': 'pending', 'started_at': now.astimezone(timezone.utc).isoformat(),
                  'recording': self.recording()}
        try:
            # Exclusive creation also prevents another program instance from
            # repeating an attempt. A crash leaves it pending until next day.
            with path.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise DailyUnavailable(f'{task} 本遊戲日已有嘗試紀錄；未重複消耗次數') from None
        return DailyAttempt(path, record)
