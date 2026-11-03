"""Recorded entry, isolated gameplay and verified exit for High & Low."""
import json
from pathlib import Path
import time

from .calendar import DailyUnavailable
from .contracts import ConfigurationError, TaskResult, TaskStatus
from .navigation import NavigationError
from .workers import WorkerSupervisor


class HighLowRunner:
    def __init__(self, navigator, root, data, progress=lambda result: None):
        self.nav, self.root, self.data, self.progress = navigator, root, data, progress

    def table(self, observation):
        return self.nav.high_low_idle_table(observation) is not None

    def invitation(self, observation):
        if (observation.scene != 'park_dialogue' and
                not self.nav.vision.match(observation.image, 'park_dialogue_next')):
            return False
        names = self.nav.text(observation, r'^(Shiori|佩克拉)$', (550, 560, 170, 50))
        if len(names) != 1:
            return False
        expected = (r'^若想抓住機會[，, ]*那就全力以赴吧[。.]?$' if names[0].text.strip() == 'Shiori'
                    else r'想玩嗎\s*PEKO')
        return len(self.nav.text(observation, expected, (200, 610, 850, 60))) == 1

    def open_table(self):
        observation = self.nav.observe()
        if self.table(observation):
            return
        if observation.scene == 'home' and len(self.nav.text(observation, '^比大小$', (700, 300, 560, 310))) == 1:
            self.nav.inputs.key(0x45, .12)
        elif not self.invitation(observation):
            raise NavigationError('未確認比大小互動入口')
        deadline, dialogue_advanced = time.monotonic()+18, False
        while time.monotonic() < deadline:
            observation = self.nav.observe()
            if self.table(observation):
                return
            if not dialogue_advanced and self.invitation(observation):
                self.nav.inputs.key(0x45, .12)
                dialogue_advanced = True
            self.nav.inputs.pause(.2)
        self.nav.save(observation, 'high-low-table-unconfirmed')
        raise NavigationError('未確認比大小牌桌，未授權投注')

    def leave_table(self):
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            observation = self.nav.observe()
            if observation.scene == 'home':
                return self.nav.wait_for({'home'}, timeout=5)
            close = self.nav.vision.match(observation.image, 'high_low_close')
            if close is not None and self.table(observation):
                x, y, w, h = close.box
                self.nav.inputs.click(x+w//2, y+h//2)
                return self.nav.wait_for({'home'}, timeout=20)
            if close is not None and self.nav.text(observation, '已達最高獲得金額', (320, 80, 670, 290)):
                ends = self.nav.text(observation, '^結束$', (930, 610, 300, 75))
                if len(ends) == 1:
                    x, y, w, h = ends[0].box
                    self.nav.inputs.click(x+w//2, y+h//2)
                    return self.nav.wait_for({'home'}, timeout=20)
            self.nav.inputs.pause(.2)
        self.nav.save(observation, 'high-low-exit-unconfirmed')
        raise NavigationError('比大小已停止，未確認安全的離開牌桌按鈕')

    def run(self, config):
        if 'high_low' in self.nav.daily_attempts.attempted_tasks():
            raise DailyUnavailable('比大小本遊戲日已有嘗試紀錄')
        path = self.root / 'config/modules.json'
        settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        python = Path(settings.get('python', self.root / 'runtime/modules/python.exe'))
        modules = Path(settings.get('module_root', self.root / 'upstream/holodori/upstream'))
        if not python.is_file() or not (modules / 'high_low/auto_bot.py').is_file():
            raise ConfigurationError('比大小獨立模組執行環境尚未準備')
        current = self.nav.observe()
        if not self.table(current) and not self.invitation(current):
            self.nav.to_high_low_entry()
        attempt = None
        hwnd = self.nav.recording.hwnd

        def ready(status):
            nonlocal attempt
            if status.get('phase') != 'window_ready' or status.get('hwnd') != hwnd:
                raise NavigationError('比大小工作程序與錄影視窗不符')
            self.open_table()
            attempt = self.nav.daily_attempts.reserve('high_low')
            self.progress(TaskResult(TaskStatus.RUNNING, '比大小牌桌已確認，準備投注', pending_round=True))
            return {'hwnd': hwnd}

        def progress(record):
            self.nav.recording.mark('module_status', **record)
            if record.get('phase') == 'settled':
                self.progress(TaskResult(TaskStatus.RUNNING, f"已確認入帳 {record['credited']} 枚硬幣",
                                         record['wins'], pending_round=True))

        worker = WorkerSupervisor(self.root, python, self.data / 'workers', module_root=modules,
            stopping=lambda: self.nav.context.tasker.stopping, guard=self.nav.inputs.check,
            progress=progress, emergency_release=lambda: self.nav.inputs.wait(self.nav.controller.post_touch_up(0)))
        try:
            outcome = worker.run(config, timeout=1800, stop_grace=2, on_ready=ready)
            result = outcome.evidence.get('result', {})
            credited, wins = result.get('credited'), result.get('wins')
            reason = result.get('reason')
            if (attempt is None or type(credited) is not int or credited < 0 or type(wins) is not int or wins < 0
                    or reason not in {'cap', 'phases'} or (reason == 'phases' and config.strategy != 'aggressive')):
                raise NavigationError('比大小未回報有效的結算或上限證據')
            evidence = self.nav.save(self.nav.observe(), 'high-low-stopped')
            attempt.finish(evidence=evidence)
            message = f'比大小已確認 {wins} 次入帳，共 {credited} 枚硬幣'
            self.progress(TaskResult(TaskStatus.RUNNING, message, wins, evidence))
            self.leave_table()
            return TaskResult(TaskStatus.COMPLETE, message, wins, evidence)
        except Exception as error:
            if attempt is not None and attempt.record['state'] == 'pending':
                attempt.finish(error=error)
            raise
