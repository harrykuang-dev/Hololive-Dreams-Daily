"""Game-provided Auto checks at the actual Live start/ready boundary."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import time

import cv2
import numpy as np

from .contracts import AutoObservation, ResourceObservation, TaskResult, TaskStatus
from .inputs import Cancelled, InputFailure
from .navigation import NavigationError
from .resources import EnergySettings, ResourceUnavailable, parse_fraction, read_one, read_setup_cost, parse_consumption
from .vision import parse_remaining


class LiveBlocked(NavigationError):
    pass


@dataclass(frozen=True)
class LiveState:
    auto: AutoObservation
    resource: ResourceObservation
    cost: int | None
    effective_cost: int | None = None


class LiveControls:
    def __init__(self, navigator):
        self.nav = navigator

    def read(self, observation):
        if observation.scene not in ('solo_setup', 'multi_ready'):
            raise LiveBlocked('目前不是 Live 開始前設定畫面')
        solo = observation.scene == 'solo_setup'
        count_roi = (420, 585, 180, 43) if solo else (145, 585, 150, 43)
        remaining = read_one(self.nav, observation, '[0-9]', count_roi)
        if parse_remaining(remaining) is None:
            # The localized prefix can be misread; retry only the numeric field.
            roi = (500, 588, 49, 37) if solo else (205, 588, 49, 37)
            remaining = read_one(self.nav, observation, '^[0-9]+(?:[.][0-9]+)?[KkMm萬]?$', roi)
        auto = self.nav.vision.auto(observation.image, observation.scene, remaining,
                                    captured_at=observation.captured_at)
        fraction = read_one(self.nav, observation, '[0-9].*[/／].*[0-9]', (1025, 25, 145, 60))
        cost = read_setup_cost(self.nav, observation, multiplayer=not solo)
        return LiveState(auto, parse_fraction(fraction), cost)

    def start(self, config, budget_remaining=None, *, energy=None):
        if config.entry_only:
            raise LiveBlocked('入口驗證模式不會開始 Live')
        if budget_remaining is None:
            budget_remaining = config.resource_budget
        expected = {'solo_live': 'solo_setup', 'multi_live': 'multi_ready'}.get(config.task)
        observation = self.nav.observe()
        if expected is None or observation.scene != expected:
            raise LiveBlocked('Live 開始畫面不符，未開始遊玩')
        buttons = self.nav.text(observation, '決定$', (1030, 610, 220, 85))
        if len(buttons) != 1:
            raise LiveBlocked('無法唯一確認 Live 決定按鈕')
        state = self.read(observation)
        zero_energy = state.cost == 0 and state.resource.available == 0
        verified_empty_policy = (energy is not None and energy.empty_energy_play
                                 and energy.resource.available == state.resource.available == 0
                                 and state.cost in {energy.cost, energy.effective_cost}
                                 and energy.multiplier == config.multiplier)
        effective_cost = 0 if zero_energy or verified_empty_policy else state.cost
        if (config.multiplier < 1 or state.cost is None or state.cost < 0 or
                (state.cost != config.multiplier and not zero_energy)):
            raise LiveBlocked('Live 實際消耗量與使用者設定不符')
        if not state.resource.permits(effective_cost, budget_remaining):
            raise LiveBlocked('Live 現有能量或本次消耗上限不足')
        # This final, fresh check immediately precedes the only start input.
        # No worker or note-input controller is involved in Live gameplay.
        if not state.auto.permits_start():
            raise LiveBlocked('未確認遊戲內建 Auto 已開啟且仍有可用次數')
        recording = getattr(self.nav, 'recording', None)
        if recording is not None:
            recording.mark('live_start_verified', task=config.task, auto=asdict(state.auto),
                           resource=asdict(state.resource), cost=effective_cost, displayed_cost=state.cost,
                           nominal_cost=energy.cost if energy is not None else state.cost,
                           empty_energy_policy_verified=verified_empty_policy, budget_remaining=budget_remaining)
        x, y, w, h = buttons[0].box
        self.nav.inputs.click(int(x+w/2), int(y+h/2))
        return replace(state, effective_cost=effective_cost)


class SoloLiveRunner:
    def __init__(self, navigator, progress=lambda result: None):
        self.nav, self.progress = navigator, progress
        self.controls = LiveControls(navigator)

    def run(self, config):
        completed, spent, pending, evidence = 0, 0, False, None
        try:
            if config.team != 'current' or config.song or config.difficulty != 'current':
                raise LiveBlocked('目前單人 Live 僅支援沿用歌曲、難度與隊伍設定')
            for number in range(1, config.rounds+1):
                self.nav.to_solo_setup()
                budget = None if config.resource_budget is None else config.resource_budget-spent
                energy = EnergySettings(self.nav).configure('solo_live', config.multiplier, budget)
                pending = True
                self.progress(TaskResult(TaskStatus.RUNNING, f'準備開始第 {number} 局 Auto Live',
                                         completed, evidence, pending_round=True))
                state = self.controls.start(config, budget, energy=energy)
                spent += state.effective_cost
                self.progress(TaskResult(TaskStatus.RUNNING, f'第 {number} 局由遊戲內建 Auto 遊玩中',
                                         completed, evidence, pending_round=True))
                result = self.wait_auto_result()
                evidence = self.nav.save(result, 'solo-auto-result')
                completed += 1
                pending = False
                self.progress(TaskResult(TaskStatus.RUNNING, f'已確認 {completed} 局 Auto Live 結果',
                                         completed, evidence))
                self.finish_result(result)
            return TaskResult(TaskStatus.COMPLETE, f'已完成 {completed} 局遊戲內建 Auto Live', completed, evidence)
        except (LiveBlocked, ResourceUnavailable) as error:
            return TaskResult(TaskStatus.SKIPPED, str(error), completed, evidence)
        except Cancelled:
            return TaskResult(TaskStatus.CANCELLED, '已停止；進行中的 Auto Live 結果尚未確認' if pending else '已停止',
                              completed, evidence, pending_round=pending)
        except (NavigationError, InputFailure) as error:
            reason = str(error)+('；已送出開始操作，本局結果尚未確認' if pending else '')
            return TaskResult(TaskStatus.FAILED, reason, completed, evidence, pending_round=pending)

    def wait_auto_result(self, timeout=600):
        deadline = time.monotonic()+timeout
        transition_deadline = time.monotonic()+15
        while True:
            observation = self.nav.observe()
            if observation.scene == 'live_result':
                auto = self.nav.text(observation, '^AUTO LIVE$', (650, 180, 530, 330))
                if len(auto) == 1:
                    return observation
            if (time.monotonic() >= transition_deadline and
                    observation.scene in {'home', 'live_portal', 'song_select', 'solo_setup'}):
                self.nav.save(observation, 'live-returned-without-result')
                raise NavigationError('Live 已返回選單，但未確認 Auto Live 結果')
            if time.monotonic() >= deadline:
                self.nav.save(observation, 'live-result-timeout')
                raise NavigationError('等待 Auto Live 結果逾時')
            # Deliberately no gameplay input. The game owns every rhythm note.
            self.nav.inputs.pause(.5)

    def finish_result(self, observation):
        for _ in range(8):
            if observation.scene in {'live_portal', 'song_select', 'solo_setup'}:
                return observation
            if observation.scene not in {'live_result', 'holowork_get', 'live_achievement', None}:
                raise NavigationError('Live 結果畫面不符')
            observation, buttons = self.wait_next_button(observation)
            if not buttons:
                return observation
            before = observation.image[180:580, 600:1200].copy()
            x, y, w, h = buttons[0].box
            self.nav.inputs.click(int(x+w/2), int(y+h/2))
            deadline = time.monotonic()+45
            retry_after, retried = time.monotonic()+4, False
            self.nav.inputs.pause(.5)
            while True:
                updated = self.nav.observe()
                if updated.scene in {'holowork_get', 'live_achievement', None}:
                    updated, _ = self.wait_next_button(updated)
                if updated.scene in {'live_portal', 'song_select', 'solo_setup'}:
                    return updated
                if updated.scene == 'live_result':
                    difference = float(np.mean(cv2.absdiff(before, updated.image[180:580, 600:1200])))
                    if difference > 8 and self.nav.text(updated, '下一步$', (1020, 610, 230, 100)):
                        observation = updated
                        break
                    if not retried and time.monotonic() >= retry_after:
                        # Result animations can consume the first Next input to
                        # fast-forward the page. Retry once only after a fresh,
                        # stable result page still exposes the same Next button.
                        stable, next_buttons = self.wait_next_button(updated)
                        if not next_buttons:
                            return stable
                        x, y, w, h = next_buttons[0].box
                        self.nav.inputs.click(int(x+w/2), int(y+h/2))
                        retried = True
                if time.monotonic() >= deadline:
                    self.nav.save(updated, 'live-result-no-progress')
                    raise NavigationError('Live 結果未能穩定進入下一頁')
                self.nav.inputs.pause(.3)
        raise NavigationError('Live 結果頁數已達安全上限')

    def wait_next_button(self, observation):
        deadline = time.monotonic()+15
        not_before, stable_since, previous = time.monotonic()+1.5, None, None
        reward_closed = False
        achievement_closed = False
        while True:
            if observation.scene in {'live_portal', 'song_select', 'solo_setup'}:
                return observation, []
            reward = self.nav.received_reward_button(observation)
            achievement = self.nav.live_achievement_button(observation) if reward is None else None
            close = reward if reward is not None and not reward_closed else (
                achievement if achievement is not None and not achievement_closed else None)
            if close is not None:
                x, y, w, h = close
                self.nav.inputs.click(int(x+w/2), int(y+h/2))
                if reward is not None:
                    reward_closed = True
                else:
                    achievement_closed = True
                    reward_closed = False
                deadline, not_before = time.monotonic()+15, time.monotonic()+1.5
                stable_since = previous = None
            elif observation.scene == 'live_result' and reward is None and achievement is None:
                buttons = self.nav.text(observation, '下一步$', (1020, 610, 230, 100))
                panel = observation.image[180:580, 600:1200]
                if previous is not None and float(np.mean(cv2.absdiff(previous, panel))) < 1:
                    if stable_since is None: stable_since = time.monotonic()
                else:
                    stable_since = None
                previous = panel.copy()
                if (len(buttons) == 1 and stable_since is not None and
                        time.monotonic()-stable_since >= .8 and time.monotonic() >= not_before):
                    return observation, buttons
            elif observation.scene not in {'holowork_get', 'live_achievement', None, 'live_result'}:
                raise NavigationError('等待 Live 下一步時畫面已改變')
            else:
                stable_since = previous = None
            if time.monotonic() >= deadline:
                self.nav.save(observation, 'live-next-not-found')
                raise NavigationError('無法唯一確認 Live 下一步按鈕')
            self.nav.inputs.pause(.3)
            observation = self.nav.observe()
