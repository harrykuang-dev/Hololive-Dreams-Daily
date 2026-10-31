"""Claim observed rewards once per page, with positive post-click confirmation."""
from __future__ import annotations

from dataclasses import dataclass
import time

import cv2
import numpy as np

from .contracts import TaskResult, TaskStatus
from .navigation import NavigationError


@dataclass(frozen=True)
class RewardView:
    observation: object
    page: str | None
    state: str
    button: tuple | None = None


def cyan_button_state(image, box):
    """Recorded enabled buttons are bright cyan; disabled ones are dimmed.

    Read the observed button rectangle, excluding its edge, badge and cursor.
    Unknown colours/transition frames never authorize a click.
    """
    x, y, w, h = map(int, box)
    hsv = cv2.cvtColor(image[max(0, y-4):min(720, y+h+4),
                             max(0, x-15):min(1280, x+w+15)], cv2.COLOR_BGR2HSV)
    cyan = hsv[:, :, 2][(hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 115)
                          & (hsv[:, :, 1] >= 120)]
    if cyan.size < 150:
        return 'unknown'
    value = float(np.median(cyan))
    return 'claimable' if value >= 245 else 'empty' if value <= 230 else 'unknown'


class RewardsRunner:
    def __init__(self, navigator, progress=lambda result: None):
        self.nav, self.progress = navigator, progress
        self.claimed = 0

    def read(self, observation=None):
        observation = observation or self.nav.observe()
        scene = observation.scene
        page = None
        if scene == 'holowork_get':
            return RewardView(observation, None, 'get')
        if scene == 'daily_missions':
            labels = {item.text.strip().replace('赛', '賽') for item in self.nav.text(
                observation, r'^(每日|每週|[賽赛]季)$', (90, 230, 200, 240))}
            if labels == {'每日', '每週', '賽季'}:
                selected = []
                for name, y in (('daily', 260), ('weekly', 340), ('season', 430)):
                    patch = observation.image[y-6:y+6, 108:123]
                    if np.all(np.median(patch, axis=(0, 1)) >= 205):
                        selected.append(name)
                if len(selected) == 1:
                    page = selected[0]
            else:
                labels = {item.text.strip() for item in self.nav.text(
                    observation, r'^(免費|豪華)$', (85, 290, 150, 260))}
                if labels == {'免費', '豪華'}:
                    page = 'pass'
        elif scene == 'event_missions':
            selected = []
            for name, x in (('event_daily', 430), ('event_free', 850)):
                underline = observation.image[170:177, x:x+320]
                hsv = cv2.cvtColor(underline, cv2.COLOR_BGR2HSV)
                if np.mean((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 115)
                           & (hsv[:, :, 1] >= 100)) >= .25:
                    selected.append(name)
            if len(selected) == 1:
                page = selected[0]
        elif scene in ('achievements', 'gifts'):
            page = scene
        if page is None:
            return RewardView(observation, None, 'unknown')
        if page == 'gifts' and self.nav.text(observation, '^沒有可領取的物品$', (400, 350, 540, 180)):
            return RewardView(observation, page, 'empty')
        # The recorded dimmed achievement label reads 健领取 in PP-OCRv5;
        # keep this variant confined to its fixed bottom-right claim control.
        expected = r'^一?[鍵键健](?:[領领])?取$' if page == 'achievements' else r'^全部[領领]取$'
        items = self.nav.text(observation, expected, (1000, 610, 230, 75))
        if not items and page == 'achievements':
            items = self.nav.text(observation, expected, (1055, 615, 120, 45), only_rec=True)
        if len(items) != 1:
            if not items and page in ('event_daily', 'event_free'):
                statuses = self.nav.text(observation, r'^(達成|[→➜]?\s*GO|[√✔]?\s*[領领]取)$', (1030, 210, 200, 370))
                if statuses and not any('取' in item.text for item in statuses):
                    return RewardView(observation, page, 'empty')
            return RewardView(observation, page, 'unknown')
        button = tuple(items[0].box)
        state = cyan_button_state(observation.image, button)
        return RewardView(observation, page, state, button)

    def stable(self, page, *, states=('claimable', 'empty'), timeout=12):
        deadline, previous, since = time.monotonic()+timeout, None, None
        while True:
            view = self.read()
            key = (view.page, view.state)
            if view.page == page and view.state in states:
                if previous != key:
                    previous, since = key, time.monotonic()
                elif time.monotonic()-since >= .5:
                    return view
            else:
                previous, since = None, None
            if time.monotonic() >= deadline:
                self.nav.save(view.observation, 'reward-state-unknown')
                raise NavigationError(f'無法確認領獎頁狀態：{page}')
            self.nav.inputs.pause(.15)

    def collect(self, page):
        view = self.stable(page)
        if view.state == 'empty':
            self.nav.save(view.observation, page+'-nothing-to-claim')
            return
        self.progress(TaskResult(TaskStatus.RUNNING, f'領取 {page} 可用獎勵',
                                 self.claimed, pending_round=True))
        self.nav.save(view.observation, page+'-before-claim')
        x, y, w, h = view.button
        self.nav.inputs.click(int(x+w/2), int(y+h/2))  # Never retry a claim.
        deadline, closed = time.monotonic()+25, False
        while True:
            view = self.read()
            if view.state == 'get' and not closed:
                close = self.nav.text(view.observation, '^點擊關閉$', (480, 620, 350, 85))
                if len(close) == 1:
                    self.nav.save(view.observation, page+'-received')
                    x, y, w, h = close[0].box
                    self.nav.inputs.click(int(x+w/2), int(y+h/2))
                    closed = True
            if view.page == page and view.state == 'empty':
                view = self.stable(page, states=('empty',))
                self.claimed += 1
                evidence = self.nav.save(view.observation, page+'-claimed')
                self.progress(TaskResult(TaskStatus.RUNNING, f'已確認 {page} 領取完成',
                                         self.claimed, evidence))
                return
            if time.monotonic() >= deadline:
                self.nav.save(view.observation, page+'-claim-unconfirmed')
                raise NavigationError(f'{page} 領取尚未確認，未再次點擊')
            self.nav.inputs.pause(.15)

    def select_missions(self, page):
        observation = self.nav.wait_for({'daily_missions'}, timeout=5)
        if self.read(observation).page == 'pass':
            self.nav.click_text('daily_missions', '^任務$', 'daily_missions', roi=(340, 125, 150, 65))
        label = {'daily': '每日', 'weekly': '每週', 'season': '[賽赛]季'}[page]
        observation = self.nav.wait_for({'daily_missions'}, timeout=5)
        if self.read(observation).page != page:
            self.nav.click_text('daily_missions', '^'+label+'$', 'daily_missions', roi=(90, 230, 200, 240))
        self.stable(page)

    def run(self):
        if self.read().page == 'pass':
            self.collect('pass')
        for page in ('daily', 'weekly', 'season'):
            self.select_missions(page)
            self.collect(page)
        self.nav.click_text('daily_missions', '^通行證$', 'daily_missions', roi=(100, 125, 150, 65))
        self.collect('pass')  # Mission points can unlock another pass reward.
        self.nav.to_home()
        self.nav.click_text('home', '^成就$', 'achievements', roi=(800, 10, 380, 110))
        self.collect('achievements')
        self.nav.to_home()
        self.nav.inputs.click(1213, 55)
        self.nav.wait_for({'main_menu'})
        self.nav.click_text('main_menu', '^禮物$', 'gifts', roi=(20, 170, 170, 300))
        self.collect('gifts')
        self.collect_event()
        evidence = self.nav.save(self.nav.to_home(), 'rewards-returned-home')
        return TaskResult(TaskStatus.COMPLETE, '已檢查任務、通行證、成就、禮物及目前活動獎勵',
                          self.claimed, evidence)

    def collect_event(self):
        self.nav.to_home()
        self.nav.click_text('home', '^活動$', 'event_list', roi=(700, 10, 200, 110))
        self.nav.click_text('event_list', r'^[→➜]?\s*GO$', 'event_home', roi=(1000, 610, 230, 85))
        self.nav.click_text('event_home', '^任務$', 'event_missions', roi=(1170, 400, 100, 70))
        for page, label, roi in (('event_daily', '每日', (560, 115, 110, 50)),
                                 ('event_free', '自由', (980, 115, 110, 50))):
            observation = self.nav.wait_for({'event_missions'}, timeout=5)
            if self.read(observation).page != page:
                self.nav.click_text('event_missions', '^'+label+'$', 'event_missions', roi=roi)
            self.collect(page)
