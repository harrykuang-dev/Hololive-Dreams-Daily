"""Collect completed work and verify a fresh dispatch through observed timers."""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

from .contracts import TaskResult, TaskStatus
from .navigation import NavigationError


@dataclass(frozen=True)
class WorkView:
    observation: object
    kind: str
    buttons: dict
    scores: tuple[int | None, ...] = ()
    empty_slots: tuple[int, ...] = ()
    timers: tuple[str, ...] = ()


def button_map(items):
    names = ('全部領取', '領取', '點擊關閉', '一鍵設定', '推薦設定', '決定')
    result = {}
    for name in names:
        candidates = [item for item in items if
                      (bool(re.search(r'一?[鍵键]設定$', getattr(item, 'text', '').strip()))
                       if name == '一鍵設定' else getattr(item, 'text', '').strip().endswith(name))
                      and not (name == '領取' and '全部領取' in item.text)]
        if len(candidates) == 1:
            result[name] = tuple(candidates[0].box)
    return result


class HoloWorkRunner:
    def __init__(self, navigator, progress=lambda result: None):
        self.nav, self.progress = navigator, progress

    def read(self, observation=None):
        observation = observation or self.nav.observe()
        if observation.scene == 'holowork_started':
            return WorkView(observation, 'started', {})
        if observation.scene == 'holowork_get':
            buttons = button_map(self.nav.text(observation, '點擊關閉$', (480, 620, 350, 85)))
            return WorkView(observation, 'get' if '點擊關閉' in buttons else 'unknown', buttons)
        if observation.scene != 'holowork':
            return WorkView(observation, 'unknown', {})
        body = self.nav.text(observation, '.*', (20, 130, 1210, 475))
        bottom = self.nav.text(observation, '.*', (650, 600, 600, 100))
        words = [getattr(item, 'text', '').strip() for item in body]
        buttons = button_map(bottom)
        if '全部領取' in buttons and any('Work完成' in word for word in words):
            return WorkView(observation, 'completed', buttons)
        if '領取' in buttons and any('獲得獎勵' in word or '獲得報酬' in word for word in words):
            return WorkView(observation, 'summary', buttons)
        if '推薦設定' in buttons:
            headings = sorted((item for item in body if item.text.strip() == 'TOTAL'
                               and 930 <= item.box[0] <= 1020 and item.box[1] < 560),
                              key=lambda item: item.box[1])
            scores, empty = [], []
            for item in headings:
                y = item.box[1]
                values = self.nav.text(observation, '^[0-9]{1,9}$', (950, y+16, 165, 55), only_rec=True)
                texts = [value.text.strip() for value in values]
                scores.append(int(texts[0]) if len(texts) == 1 and re.fullmatch('[0-9]{1,9}', texts[0]) else None)
                empty.append(len(self.nav.vision.find_all(observation.image, 'work_empty_slot',
                                                         roi=(450, y-22, 490, 105))))
            if len(headings) >= 3:
                return WorkView(observation, 'assignment', buttons, tuple(scores), tuple(empty))
        if '一鍵設定' in buttons and any('選擇holo成員' in word for word in words):
            return WorkView(observation, 'available', buttons)
        timers = tuple(word for word in words if re.fullmatch(r'[0-9]{1,2}:[0-5][0-9]:[0-5][0-9]', word))
        stops = sum('停止' in word for word in words)
        if len(timers) >= 3 and stops >= 3:
            return WorkView(observation, 'working', buttons, timers=timers)
        return WorkView(observation, 'unknown', buttons)

    def wait(self, kinds, *, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            view = self.read()
            if view.kind in kinds:
                return view
            if time.monotonic() >= deadline:
                self.nav.save(view.observation, 'holowork-stalled')
                raise NavigationError(f'holoWork 等待狀態逾時：{", ".join(sorted(kinds))}')
            self.nav.inputs.pause(.2)

    def click(self, view, name):
        if name not in view.buttons:
            raise NavigationError(f'無法唯一確認 holoWork 按鈕：{name}')
        x, y, w, h = view.buttons[name]
        self.nav.inputs.click(int(x+w/2), int(y+h/2))

    def stable_assignment(self, view, *, timeout=20):
        deadline = time.monotonic() + timeout
        previous, stable_since = None, None
        while True:
            ready = (view.kind == 'assignment' and len(view.scores) >= 3
                     and all(type(score) is int and score > 0 for score in view.scores)
                     and not any(view.empty_slots) and '決定' in view.buttons)
            if ready:
                if view.scores != previous:
                    previous, stable_since = view.scores, time.monotonic()
                elif time.monotonic() - stable_since >= .6:
                    return view
            else:
                previous, stable_since = None, None
            if time.monotonic() >= deadline:
                self.nav.save(view.observation, 'holowork-team-not-ready')
                raise NavigationError('holoWork 隊伍或 TOTAL 數值尚未穩定，未送出派遣')
            self.nav.inputs.pause(.2)
            view = self.read()

    def run(self):
        view = self.wait({'completed', 'summary', 'get', 'available', 'assignment', 'working'})
        collected = False
        if view.kind == 'working':
            evidence = self.nav.save(view.observation, 'holowork-already-working')
            return TaskResult(TaskStatus.COMPLETE, '已確認 holoWork 正在執行', evidence=evidence)
        if view.kind == 'completed':
            self.progress(TaskResult(TaskStatus.RUNNING, '收取已完成的 holoWork'))
            self.click(view, '全部領取')
            view = self.wait({'summary'})
        if view.kind == 'summary':
            self.click(view, '領取')
            view = self.wait({'get'})
        if view.kind == 'get':
            self.nav.save(view.observation, 'holowork-collected')
            self.click(view, '點擊關閉')
            collected = True
            view = self.wait({'available', 'working'})
        if view.kind == 'working':
            evidence = self.nav.save(view.observation, 'holowork-working-after-collection')
            return TaskResult(TaskStatus.COMPLETE, '已收取報酬並確認其餘工作執行中', evidence=evidence)
        if view.kind == 'available':
            self.click(view, '一鍵設定')
            view = self.wait({'assignment'})
        if view.kind != 'assignment':
            raise NavigationError('holoWork 尚未到達隊伍設定畫面')
        if all(score is None for score in view.scores) and all(count == 5 for count in view.empty_slots):
            self.click(view, '推薦設定')
            view = self.wait({'assignment'})
        elif any(view.empty_slots) or any(score is None for score in view.scores):
            raise NavigationError('holoWork 隊伍只有部分設定完成；保留現有成員並停止')
        view = self.stable_assignment(view)
        self.progress(TaskResult(TaskStatus.RUNNING, '已確認隊伍穩定，準備派遣 holoWork'))
        self.nav.save(view.observation, 'holowork-ready-to-dispatch')
        self.click(view, '決定')  # Never retry a dispatch input.
        view = self.wait({'started', 'working'})
        if view.kind == 'started':
            self.nav.save(view.observation, 'holowork-started')
            view = self.wait({'working'})
        evidence = self.nav.save(view.observation, 'holowork-dispatched')
        reason = '已收取 holoWork 報酬並確認重新派遣' if collected else '已確認 holoWork 派遣'
        return TaskResult(TaskStatus.COMPLETE, reason, completed_rounds=1, evidence=evidence)
