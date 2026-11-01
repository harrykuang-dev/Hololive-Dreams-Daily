"""One isolated Jump Rope round per fresh parent resource authorization."""
from dataclasses import replace
import json
from pathlib import Path

from .contracts import ConfigurationError, TaskResult, TaskStatus
from .navigation import NavigationError
from .resources import EnergySettings
from .minigames import MinigameResults, start_minigame
from .workers import WorkerSupervisor


class JumpRunner(MinigameResults):
    def __init__(self, navigator, root: Path | None = None, data: Path | None = None, progress=lambda result: None,
                 supervisor=WorkerSupervisor):
        super().__init__(navigator, 'jump_rope')
        self.root, self.data = root, data
        self.progress, self.supervisor = progress, supervisor

    def release_space(self):
        self.nav.inputs.wait(self.nav.controller.post_key_up(0x20))
        if self.nav.recording:
            self.nav.recording.mark('module_key_up', key=0x20)

    def start_round(self, status, energy, budget, before_input=lambda: None):
        return start_minigame(self.nav, 'jump_rope', status, energy, budget, before_input)

    def run(self, config):
        if config.task != 'jump_rope' or config.entry_only or self.root is None or self.data is None:
            raise ConfigurationError('跳繩執行設定不符')
        if config.score_limit is not None and not 1 <= config.score_limit <= 999:
            raise ConfigurationError('跳繩分數上限必須介於1至999')
        path = self.root / 'config/modules.json'
        settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        python = Path(settings.get('python', self.root / 'runtime/modules/python.exe'))
        modules = Path(settings.get('module_root', self.root / 'upstream/holodori/upstream'))
        if not python.is_file() or not (modules / 'jump_rope/batch_session.py').is_file():
            raise ConfigurationError('獨立跳繩執行環境尚未準備')
        completed = spent = 0
        evidence = None
        def progress(record):
            if self.nav.recording:
                self.nav.recording.mark('module_status', **record)
        worker = self.supervisor(self.root, python, self.data / 'workers',
            stopping=lambda: self.nav.context.tasker.stopping, guard=self.nav.inputs.check,
            progress=progress, module_root=modules, emergency_release=self.release_space)
        for number in range(1, config.rounds+1):
            self.nav.to_minigame_setup('jump_rope')
            budget = None if config.resource_budget is None else config.resource_budget-spent
            energy = EnergySettings(self.nav).configure('jump_rope', config.multiplier, budget)
            self.progress(TaskResult(TaskStatus.RUNNING, f'正在準備第{number}局跳繩模組', completed, evidence))
            def starting():
                self.progress(TaskResult(TaskStatus.RUNNING, f'第{number}局跳繩即將開始',
                                         completed, evidence, pending_round=True))
            def ready(status):
                nonlocal spent
                metadata = self.start_round(status, energy, budget, starting)
                spent += metadata['cost']
                return metadata
            try:
                outcome = worker.run(replace(config, rounds=1), on_ready=ready, timeout=960, stop_grace=2)
            finally:
                self.release_space()
            if outcome.completed != 1:
                raise NavigationError('跳繩模組完成局數不符')
            result, _ = self.result_page()
            evidence = self.nav.save(result, 'jump-round-result')
            completed += 1
            self.progress(TaskResult(TaskStatus.RUNNING, '已確認跳繩結果，正在離開結果頁', completed, evidence))
            self.finish_result()
        return TaskResult(TaskStatus.COMPLETE, f'已確認{completed}局跳繩結果並返回設定頁', completed, evidence)
