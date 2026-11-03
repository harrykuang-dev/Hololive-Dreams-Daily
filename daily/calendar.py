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
        self.replace(updated)

    def replace(self, updated):
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
        directory = data / 'attempts'
        if 'attempt_directory' in settings:
            value = settings['attempt_directory']
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError('Daily attempt directory must be an absolute path')
            directory = Path(value)
        return cls(directory, not_before=settings.get('daily_test_not_before'),
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

    def attempted_tasks(self):
        directory = self.directory / game_day(self.clock())
        return {path.stem for path in directory.glob('*.json') if path.is_file()}

    def resume_open_dialogue(self, task: str, *, verify_gift=None) -> DailyAttempt:
        # Lottery callers confirm the introduction. Gift recovery additionally
        # compares the current handover with the original recording.
        gift = task == 'park_gift_holo_house' and callable(verify_gift)
        if task != 'lottery' and not gift:
            raise DailyUnavailable('此每日項目未啟用對話接續')
        self.guard()
        now = self.clock()
        day = game_day(now)
        if self.not_before is not None and now < max(self.not_before, test_window_start(now)):
            raise DailyUnavailable('每日對話接續尚未到實測時間')
        path = self.directory / day / f'{task}.json'
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if (record.get('task') != task or record.get('game_day') != day or
                    record.get('state') != 'failed' or record.get('recovered_at')):
                raise ValueError('Attempt cannot be resumed')
            previous = record['recording']
            video = Path(previous['video'])
            if not video.is_file():
                raise ValueError('Original recording is missing')
            ready = started = closed = False
            frames = None
            released = False
            with Path(previous['timeline']).open(encoding='utf-8') as stream:
                for line in stream:
                    event = json.loads(line)
                    if gift and started and event.get('event') in {'key_down', 'key_up', 'click', 'mouse_move'}:
                        if not released and event['event'] == 'key_up' and event.get('key') == 0x45:
                            released = True
                        else:
                            raise ValueError('Original handover contains additional input')
                    if event.get('event') == 'recording_ready':
                        ready = True
                    elif event.get('event') == 'key_down' and event.get('key') == 0x45:
                        if gift and (not ready or closed or datetime.fromisoformat(event['utc'])
                                     < datetime.fromisoformat(record['started_at'])):
                            raise ValueError('Original E does not belong to the reserved attempt')
                        started |= ready
                    elif event.get('event') == 'recording_stopped':
                        closed |= started and not event.get('capture_error')
                        frames = event.get('frames')
            if not started or not closed:
                raise ValueError('Original interaction recording is not finalized')
            if gift and (not released or type(frames) is not int or frames <= 0):
                raise ValueError('Original handover lacks verified frame or input evidence')
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise DailyUnavailable('沒有可接續且已錄影的本日嘗試，未輸入') from None
        if gift and not verify_gift(video, frames):
            raise DailyUnavailable('禮物對話與原錄影不一致，未輸入')
        self.guard()
        now = self.clock()
        if game_day(now) != day:
            raise DailyUnavailable('遊戲日期已變更，未接續本次互動')
        recovery = {'resumed_at': now.astimezone(timezone.utc).isoformat(),
                    'recording': self.recording()}
        try:
            with path.with_suffix('.recovery').open('x', encoding='utf-8') as stream:
                json.dump(recovery, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise DailyUnavailable('本日對話已接續過，未重試') from None
        attempt = DailyAttempt(path, record)
        attempt.replace({**record, 'state': 'pending', 'recovered_at': recovery['resumed_at'],
                         'recovery_recording': recovery['recording'],
                         'prior_outcome': {key: record.get(key) for key in ('state', 'error', 'finished_at')}})
        return attempt
