"""One isolated Jump Rope round per fresh parent resource authorization."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import time

from .contracts import ConfigurationError, TaskResult, TaskStatus
from .navigation import NavigationError
from .resources import EnergySettings, ResourceUnavailable, parse_fraction, read_one, read_setup_cost
from .workers import WorkerSupervisor


class JumpRunner:
    def __init__(self, navigator, root: Path | None = None, data: Path | None = None, progress=lambda result: None,
                 supervisor=WorkerSupervisor):
        self.nav, self.root, self.data = navigator, root, data
        self.progress, self.supervisor = progress, supervisor

    def release_space(self):
        self.nav.inputs.wait(self.nav.controller.post_key_up(0x20))
        if self.nav.recording:
            self.nav.recording.mark('module_key_up', key=0x20)

    def start_round(self, status, energy, budget, before_input=lambda: None):
        if status.get('phase') != 'capture_ready':
            raise NavigationError('跳繩模組尚未確認擷取就緒')
        observation = self.nav.observe()
        if observation.scene != 'jump_setup':
            raise NavigationError('跳繩開始前畫面不符')
        buttons = self.nav.text(observation, '^遊玩$', (980, 610, 260, 85))
        resource = parse_fraction(read_one(self.nav, observation, '[0-9].*[/／].*[0-9]',
                                           (1025, 25, 145, 60)))
        cost = read_setup_cost(self.nav, observation)
        if len(buttons) != 1:
            raise NavigationError('無法唯一確認跳繩遊玩按鈕')
        if (cost != energy.effective_cost or not resource.permits(cost, budget)
                or (cost == 0 and resource.available != 0)):
            raise ResourceUnavailable('跳繩實際消耗或本次資源上限不符，未開始遊玩')
        age = time.monotonic()-observation.captured_at
        if not 0 <= age <= 2:
            raise NavigationError('跳繩開始前的資源畫面已過期')
        metadata = {'task': 'jump_rope', 'cost': cost, 'resource': asdict(resource),
                    'budget_remaining': budget}
        if self.nav.recording:
            self.nav.recording.mark('minigame_start_verified', **metadata)
        before_input()
        x, y, w, h = buttons[0].box
        self.nav.inputs.click(int(x+w/2), int(y+h/2))
        return metadata

    def result_page(self, *, timeout=15):
        deadline = time.monotonic()+timeout
        while True:
            observation = self.nav.observe()
            if observation.scene == 'jump_result':
                title = self.nav.text(observation, r'^SCORE\s*ATTACK$', (600, 130, 600, 200))
                next_buttons = self.nav.text(observation, '下一步$', (980, 610, 260, 90))
                if len(title) == len(next_buttons) == 1:
                    return observation, next_buttons[0].box
            if time.monotonic() >= deadline:
                self.nav.save(observation, 'jump-result-not-confirmed')
                raise NavigationError('跳繩模組結束後未確認分數結果頁')
            self.nav.inputs.pause(.2)

    def finish_result(self):
        current = self.nav.observe()
        if button := self.reward_button(current):
            return self.close_reward(button)
        if button := self.event_button(current):
            return self.finish_event(current, button)
        self.nav.inputs.pause(.8)
        _, button = self.result_page()
        x, y, w, h = button
        self.nav.inputs.click(int(x+w/2), int(y+h/2))
        deadline = time.monotonic()+15
        while True:
            updated = self.nav.observe()
            if button := self.reward_button(updated):
                self.nav.save(updated, 'jump-rewards-confirmed')
                return self.close_reward(button)
            if time.monotonic() >= deadline:
                self.nav.save(updated, 'jump-rewards-not-confirmed')
                raise NavigationError('跳繩獎勵頁尚未確認，未重複下一步')
            self.nav.inputs.pause(.2)

    def reward_button(self, observation):
        if observation.scene != 'jump_result':
            return None
        labels = self.nav.text(observation, '(本次跳躍次數|獲得獎勵)$', (610, 220, 340, 210))
        if any(sum(item.text.strip().endswith(label) for item in labels) != 1
               for label in ('本次跳躍次數', '獲得獎勵')):
            return None
        buttons = self.nav.text(observation, '^OK[!！]?$', (980, 610, 260, 90))
        return buttons[0].box if len(buttons) == 1 else None

    def close_reward(self, button):
        x, y, w, h = button
        self.nav.inputs.click(int(x+w/2), int(y+h/2))
        return self.finish_event()

    def event_button(self, observation):
        if observation.scene != 'jump_result':
            return None
        labels = self.nav.text(observation, '^(活動加成|已持有的活動加成指定成員|已解鎖全部劇情)$',
                               (650, 220, 550, 400))
        words = {item.text.strip() for item in labels}
        if len(words.intersection({'活動加成', '已持有的活動加成指定成員', '已解鎖全部劇情'})) < 2:
            return None
        buttons = self.nav.text(observation, '下一步$', (980, 610, 260, 90))
        return buttons[0].box if len(buttons) == 1 else None

    def finish_event(self, observation=None, button=None):
        deadline = time.monotonic()+45
        clicked = False
        while True:
            observation = observation or self.nav.observe()
            if observation.scene in {'jump_setup', 'home'}:
                return self.nav.wait_for({'jump_setup', 'home'}, timeout=5)
            button = button or self.event_button(observation)
            if button is not None and not clicked:
                self.nav.save(observation, 'jump-event-result')
                x, y, w, h = button
                self.nav.inputs.click(int(x+w/2), int(y+h/2))
                clicked = True
            if time.monotonic() >= deadline:
                self.nav.save(observation, 'jump-result-return-stalled')
                raise NavigationError('跳繩結果尚未返回設定頁，未重複確認')
            self.nav.inputs.pause(.2)
            observation = button = None

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
