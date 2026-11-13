"""One verified cooking round per isolated worker and resource preflight."""
from dataclasses import replace
import json
from pathlib import Path

from .contracts import ConfigurationError, TaskResult, TaskStatus
from .minigames import MinigameResults, start_minigame
from .navigation import NavigationError
from .resources import EnergySettings, ResourceUnavailable, record_post_round_energy
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

    def run(self, config, *, required_cost=None, daily_ledger=None):
        if config.task != 'cooking' or config.entry_only:
            raise ConfigurationError('料理執行設定不符')
        if daily_ledger is not None and required_cost is None:
            raise ConfigurationError('每日料理須指定每局消耗')
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
            key = f'routine_cooking_{number}'
            if daily_ledger is not None:
                if key in daily_ledger.completed_tasks():
                    completed += 1
                    spent += required_cost
                    continue
                if key in daily_ledger.attempted_tasks():
                    raise ResourceUnavailable(f'今日第 {number} 局料理的結果未確認，未重開')
            self.nav.to_minigame_setup('cooking')
            budget = None if config.resource_budget is None else config.resource_budget-spent
            options = {} if required_cost is None else {'required_cost': required_cost}
            energy = EnergySettings(self.nav).configure('cooking', config.multiplier, budget, **options)
            self.progress(TaskResult(TaskStatus.RUNNING, f'正在準備第{number}局料理模組', completed, evidence))
            attempt = None

            def starting():
                nonlocal attempt
                if daily_ledger is not None:
                    attempt = daily_ledger.reserve(key)
                self.progress(TaskResult(TaskStatus.RUNNING, f'第{number}局料理即將開始',
                                         completed, evidence, pending_round=True))
            def ready(status):
                nonlocal spent
                metadata = start_minigame(self.nav, 'cooking', status, energy, budget, starting)
                spent += metadata['cost']
                return metadata
            try:
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
            except Exception as error:
                if attempt is not None:
                    attempt.finish(error=error)
                raise
            if attempt is not None:
                attempt.finish(evidence=evidence)
            completed += 1
            self.progress(TaskResult(TaskStatus.RUNNING, '已確認料理結果，正在離開結果頁', completed, evidence))
            returned = self.finish_result()
            record_post_round_energy(self.nav, returned, task='cooking', round_number=number)
        return TaskResult(TaskStatus.COMPLETE, f'已確認{completed}局料理結果並離開結果頁', completed, evidence)
