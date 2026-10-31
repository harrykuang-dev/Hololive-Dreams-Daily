"""Finite practice-bait fishing through the isolated upstream controller."""
from dataclasses import replace
import json
from pathlib import Path

from .contracts import ConfigurationError, TaskResult, TaskStatus
from .navigation import NavigationError
from .resources import ResourceUnavailable
from .workers import WorkerSupervisor


class FishingRunner:
    def __init__(self, navigator, root: Path, data: Path, progress=lambda result: None,
                 supervisor=WorkerSupervisor):
        self.nav, self.root, self.data = navigator, root, data
        self.progress, self.supervisor = progress, supervisor

    def verify_practice_bait(self, observation):
        if observation.scene != 'fishing_setup_second':
            raise NavigationError('未確認第二釣點設定畫面')
        label = self.nav.text(observation, '^[練练][餌饵]$', (1050, 535, 170, 45))
        infinite = self.nav.vision.match(observation.image, 'fishing_practice_infinite')
        place = self.nav.text(observation, '^菈米的冰天雪堡$', (820, 97, 240, 50), only_rec=True)
        if len(label) != 1 or infinite is None or len(place) != 1:
            raise ResourceUnavailable('目前僅啟用第二釣點的無限練餌；未更換或補充魚餌')

    def release_mouse(self):
        # The fishing worker owns only the left mouse button. Release is still
        # allowed after capture failure, cancellation or forced termination.
        self.nav.inputs.wait(self.nav.controller.post_touch_up(0))
        if self.nav.recording:
            self.nav.recording.mark('module_mouse_up', contact=0)

    def run(self, config):
        if config.target is None:
            raise ConfigurationError('釣魚必須設定有限的目標釣獲數')
        self.verify_practice_bait(self.nav.observe())
        path = self.root / 'config/modules.json'
        settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        python = Path(settings.get('python', self.root / 'runtime/modules/python.exe'))
        modules = Path(settings.get('module_root', self.root / 'upstream/holodori/upstream'))
        if not python.is_file() or not (modules / 'fishing/auto_fishing.py').is_file():
            raise ConfigurationError('獨立小遊戲執行環境尚未準備')
        completed = 0
        def progress(record):
            nonlocal completed
            catches = record.get('progress', {}).get('catches', record.get('completed', completed))
            if type(catches) is int and completed <= catches <= config.target:
                completed = catches
            self.progress(TaskResult(TaskStatus.RUNNING, '獨立釣魚模組：'+record['state'],
                                     completed, pending_round=completed < config.target))
            if self.nav.recording:
                self.nav.recording.mark('module_status', **record)
        worker = self.supervisor(self.root, python, self.data / 'workers',
            stopping=lambda: self.nav.context.tasker.stopping,
            guard=self.nav.inputs.check, progress=progress,
            module_root=modules, emergency_release=self.release_mouse)
        self.progress(TaskResult(TaskStatus.RUNNING, '即將以無限練餌釣魚', pending_round=True))
        try:
            outcome = worker.run(replace(config, rounds=1), timeout=630, stop_grace=1.5)
        finally:
            self.release_mouse()
        if outcome.completed != config.target:
            raise NavigationError('釣獲數與設定目標不符')
        self.progress(TaskResult(TaskStatus.RUNNING, '已確認目標魚獲，正在結束釣魚', completed))
        result = self.nav.wait_for({'fishing_result'}, timeout=12)
        evidence = self.nav.save(result, 'fishing-target-reached')
        self.nav.click_text('fishing_result', r'^[×xX✕]?\s*取消$', 'home', roi=(720, 620, 230, 80))
        return TaskResult(TaskStatus.COMPLETE, '已使用無限練餌達成目標並離開釣魚', completed, evidence)
