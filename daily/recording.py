"""Game-window recordings with a durable timeline and a fail-closed input guard."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import cv2

from .vision import Vision


class RecordingError(RuntimeError):
    pass


class FrameRecording:
    FPS = 5
    MAX_FRAME_AGE = 3.0

    def __init__(self, controller, directory: Path, label: str):
        self.controller = controller
        directory.mkdir(parents=True, exist_ok=True)
        stem = f'{label}-{uuid.uuid4().hex[:8]}'
        self.video = directory / f'{stem}.avi'
        self.timeline = directory / f'{stem}.jsonl'
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._error = None
        self._last_frame = None
        self._started = time.monotonic()
        self.frames = 0

    def paths(self):
        return {'video': str(self.video), 'timeline': str(self.timeline), 'fps': self.FPS}

    def mark(self, event, **details):
        entry = {'utc': datetime.now(timezone.utc).isoformat(),
                 'elapsed_seconds': round(time.monotonic() - self._started, 4),
                 'event': event, **details}
        with self._lock, self.timeline.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
            stream.flush()

    def start(self):
        if self._thread is not None:
            raise RecordingError('錄影已啟動')
        self.mark('recording_start', **self.paths())
        self._thread = threading.Thread(target=self._run, name='game-recording', daemon=True)
        self._thread.start()
        if not self._ready.wait(8):
            self._stop.set()
            raise RecordingError('錄影啟動逾時，未開始遊戲操作')
        self.check()
        self.mark('recording_ready', frames=self.frames)

    def check(self):
        if self._error is not None:
            raise RecordingError(f'錄影中斷：{self._error}') from self._error
        if (self._thread is None or not self._thread.is_alive() or self._stop.is_set()
                or self._last_frame is None):
            raise RecordingError('尚未確認遊戲畫面已開始錄影')
        if time.monotonic() - self._last_frame > self.MAX_FRAME_AGE:
            raise RecordingError('錄影超過三秒未取得新畫面，停止遊戲操作')

    def _capture(self):
        job = self.controller.post_screencap().wait()
        if not job.succeeded:
            raise RecordingError('無法擷取錄影畫面')
        return Vision.normalize(job.get())

    def _run(self):
        writer = None
        try:
            image = self._capture()
            # Check the actual encoder/decoder before acknowledging readiness.
            # An opened VideoWriter alone does not prove it can encode a frame.
            probe = self.video.with_suffix('.check.avi')
            encoder = cv2.VideoWriter(str(probe), cv2.VideoWriter_fourcc(*'MJPG'),
                                      self.FPS, (1280, 720))
            try:
                if not encoder.isOpened():
                    raise RecordingError('無法建立錄影編碼器')
                encoder.write(image)
            finally:
                encoder.release()
            decoder = cv2.VideoCapture(str(probe))
            try:
                valid, decoded = decoder.read()
                if not valid or decoded.shape[:2] != (720, 1280):
                    raise RecordingError('錄影啟動前編碼驗證失敗')
            finally:
                decoder.release()
                probe.unlink(missing_ok=True)
            writer = cv2.VideoWriter(str(self.video), cv2.VideoWriter_fourcc(*'MJPG'),
                                     self.FPS, (1280, 720))
            if not writer.isOpened():
                raise RecordingError('無法建立錄影編碼器')
            first = time.monotonic()
            previous = image
            while True:
                captured = time.monotonic()
                # Duplicate the last frame for a capture gap so video time stays
                # aligned with wall time. The timeline retains actual captures.
                index = int((captured - first) * self.FPS)
                while self.frames < index:
                    writer.write(previous)
                    self.frames += 1
                writer.write(image)
                self.frames += 1
                previous = image
                self._last_frame = captured
                self.mark('frame', frame_index=self.frames - 1,
                          video_seconds=(self.frames - 1) / self.FPS)
                self._ready.set()
                if self._stop.is_set():
                    break
                self._stop.wait(max(0, first + self.frames / self.FPS - time.monotonic()))
                image = self._capture()
        except Exception as error:
            self._error = error
        finally:
            if writer is not None:
                writer.release()
            self._ready.set()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(8)
            if self._thread.is_alive():
                raise RecordingError('錄影擷取尚未結束，影片未驗證')
        cap = cv2.VideoCapture(str(self.video))
        try:
            ok, image = cap.read()
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if not ok or image.shape[:2] != (720, 1280) or frames != self.frames:
                raise RecordingError('錄影檔解碼或影格數驗證失敗')
        finally:
            cap.release()
        self.mark('recording_stopped', frames=frames, video_seconds=frames / self.FPS,
                  capture_error=str(self._error) if self._error else None)
        if self._error is not None:
            raise RecordingError(f'錄影中斷：{self._error}') from self._error


def recording_directory(root: Path, data: Path) -> Path:
    settings = root / 'config' / 'recording.json'
    if not settings.exists():
        return data / 'recordings'
    config = json.loads(settings.read_text(encoding='utf-8'))
    path = config.get('directory')
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise RecordingError('錄影目錄必須為絕對路徑')
    return Path(path)


class GameRecording:
    """Independent capture process; never share the agent's IPC across threads."""
    def __init__(self, controller, directory: Path, label: str):
        info = controller.info
        if info.get('type') != 'win32' or type(info.get('hwnd')) is not int or info['hwnd'] <= 0:
            raise RecordingError('錄影僅支援已確認的 Windows 遊戲視窗')
        self.hwnd = info['hwnd']
        self.job_id = uuid.uuid4().hex
        self.directory = directory / f'{label}-{self.job_id[:8]}'
        self.directory.mkdir(parents=True, exist_ok=False)
        self.timeline = self.directory / 'events.jsonl'
        self.status_file = self.directory / 'status.json'
        self.stop_file = self.directory / 'stop'
        self._process = None
        self._log = None
        self._capture_paths = {}
        self._last_status = None
        self._started = time.monotonic()

    def mark(self, event, **details):
        with self.timeline.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
                                    'elapsed_seconds': round(time.monotonic() - self._started, 4),
                                    'event': event, **details}, ensure_ascii=False) + '\n')
            stream.flush()

    def paths(self):
        return {'video': self._capture_paths.get('video'), 'timeline': str(self.timeline),
                'frame_timeline': self._capture_paths.get('timeline'), 'fps': FrameRecording.FPS}

    def _status(self):
        try:
            status = json.loads(self.status_file.read_text(encoding='utf-8'))
            if status.get('job_id') != self.job_id:
                return None
            self._last_status = status
            return status
        except (OSError, ValueError, AttributeError):
            # A cached heartbeat is usable only within the same three-second
            # freshness check. A transient read race is not a new heartbeat.
            return self._last_status

    def start(self):
        if self._process is not None:
            raise RecordingError('錄影已啟動')
        config = self.directory / 'capture.json'
        config.write_text(json.dumps({'hwnd': self.hwnd, 'job_id': self.job_id,
                                      'parent_pid': os.getpid()}), encoding='utf-8')
        root = Path(__file__).resolve().parents[1]
        self._log = (self.directory / 'capture.log').open('w', encoding='utf-8')
        self.mark('recording_start')
        self._process = subprocess.Popen(
            [sys.executable, '-I', '-B', '-X', 'utf8', str(root / 'agent/recording_worker.py'),
             '--config', str(config)],
            cwd=root, stdout=self._log, stderr=self._log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            env={**os.environ, 'PYTHONUTF8': '1'})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = self._status()
            if status and status.get('state') == 'ready':
                paths = status.get('paths', {})
                for name in ('video', 'timeline'):
                    if not Path(paths.get(name, '')).resolve().is_relative_to(self.directory.resolve()):
                        raise RecordingError('錄影程序回報的檔案位置無效')
                self._capture_paths = paths
                self.check()
                self.mark('recording_ready', **self.paths())
                return
            if self._process.poll() is not None:
                raise RecordingError((status or {}).get('error', '錄影程序未能啟動'))
            time.sleep(.05)
        raise RecordingError('錄影啟動逾時，未開始遊戲操作')

    def check(self):
        status = self._status()
        if self._process is None or self._process.poll() is not None:
            raise RecordingError((status or {}).get('error', '錄影程序已停止'))
        if not status or status.get('state') != 'ready':
            raise RecordingError((status or {}).get('error', '尚未確認遊戲畫面已開始錄影'))
        updated = status.get('updated_monotonic', 0)
        if not 0 <= time.monotonic() - updated <= FrameRecording.MAX_FRAME_AGE:
            raise RecordingError('錄影超過三秒未更新，停止遊戲操作')

    def stop(self):
        try:
            if self._process is None:
                raise RecordingError('錄影程序尚未啟動')
            self.stop_file.touch()
            try:
                self._process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                self._process.wait(timeout=5)
                raise RecordingError('錄影程序未能正常結束，影片未驗證')
            status = self._status()
            if self._process.returncode != 0 or not status or status.get('state') != 'complete':
                raise RecordingError((status or {}).get('error', '錄影未完整驗證'))
            self.mark('recording_stopped', frames=status['frames'], **self.paths())
        finally:
            if self._log:
                self._log.close()
