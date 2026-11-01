"""One verified cooking round per isolated worker and resource preflight."""
from dataclasses import replace
import json
from pathlib import Path

from .contracts import ConfigurationError, TaskResult, TaskStatus
from .minigames import MinigameResults, start_minigame
from .navigation import NavigationError
from .resources import EnergySettings
from .workers import WorkerSupervisor


class CookingRunner(MinigameResults):
    def __init__(self, navigator, root: Path, data: Path, progress=lambda result: None,
                 supervisor=WorkerSupervisor):
        super().__init__(navigator, 'cooking')
        self.root, self.data = root, data
        self.progress, self.supervisor = progress, supervisor

    def release_mouse(self):
        self.nav.inputs.wait(self.nav.controller.post_touch_up(0))
        if self.nav.recording:
            self.nav.recording.mark('module_mouse_up', contact=0)

    def run(self, config):
        if config.task != 'cooking' or config.entry_only:
            raise ConfigurationError('料理執行設定不符')
        path = self.root/'config/modules.json'
        settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        python = Path(settings.get('python', self.root/'runtime/modules/python.exe'))
        modules = Path(settings.get('module_root', self.root/'upstream/holodori/upstream'))
        if not python.is_file() or not (modules/'cooking/outputs/kitchen-bot/standalone.py').is_file():
            raise ConfigurationError('獨立料理執行環境尚未準備')
        completed = spent = 0
        evidence = None
        def progress(record):
            if self.nav.recording:
                self.nav.recording.mark('module_status', **record)
        worker = self.supervisor(self.root, python, self.data/'workers',
            stopping=lambda: self.nav.context.tasker.stopping, guard=self.nav.inputs.check,
            progress=progress, module_root=modules, emergency_release=self.release_mouse)
        for number in range(1, config.rounds+1):
            self.nav.to_minigame_setup('cooking')
            budget = None if config.resource_budget is None else config.resource_budget-spent
            energy = EnergySettings(self.nav).configure('cooking', config.multiplier, budget)
            self.progress(TaskResult(TaskStatus.RUNNING, f'正在準備第{number}局料理模組', completed, evidence))
            def starting():
                self.progress(TaskResult(TaskStatus.RUNNING, f'第{number}局料理即將開始',
                                         completed, evidence, pending_round=True))
            def ready(status):
                nonlocal spent
                metadata = start_minigame(self.nav, 'cooking', status, energy, budget, starting)
                spent += metadata['cost']
                return metadata
            try:
                outcome = worker.run(replace(config, rounds=1), on_ready=ready, timeout=510, stop_grace=2)
            finally:
                self.release_mouse()
            reading = outcome.evidence.get('result', {})
            if (outcome.completed != 1 or reading.get('verified') is not True
                    or any(type(reading.get(field)) is not int or reading[field] < 0
                           for field in ('score', 'completed_dishes'))):
                raise NavigationError('料理模組未回報有效的完成局數、分數與料理數量')
            result, _ = self.result_page()
            evidence = self.nav.save(result, 'cooking-round-result')
            completed += 1
            self.progress(TaskResult(TaskStatus.RUNNING, '已確認料理結果，正在離開結果頁', completed, evidence))
            self.finish_result()
        return TaskResult(TaskStatus.COMPLETE, f'已確認{completed}局料理結果並離開結果頁', completed, evidence)
