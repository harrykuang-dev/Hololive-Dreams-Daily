"""Recorded notification teleports and once-daily gift/scratch interactions."""
from __future__ import annotations

from dataclasses import dataclass
import time

from .contracts import TaskResult, TaskStatus
from .navigation import EntryUnavailable, NavigationError


@dataclass(frozen=True)
class ParkPlace:
    key: str
    notice: str
    prompt: str
    speaker: str | None = None


GIFT_PLACES = (
    ParkPlace('park_gift_holo_house', r'^可在holo之家領取禮物$', r'禮物$'),
    ParkPlace('park_gift_the_cell', r'^可在The\s*Cell\s*重臨領取禮物$', r'禮物$', r'^[Bb]ijou$'),
    ParkPlace('park_gift_rock_mountain', r'^可在.{1,3}岩山領取禮物$', r'禮物$', r'^[Aa][Zz][Kk][Ii]$'),
)
LOTTERY_PLACE = ParkPlace('lottery', r'^可遊玩洽馬洽馬極運輪盤$', r'^刮刮樂$', r'^心$')


@dataclass(frozen=True)
class ParkView:
    kind: str
    observation: object
    button: tuple | None = None
    signature: str = ''


class ParkRunner:
    def __init__(self, navigator, progress=lambda result: None):
        self.nav, self.progress = navigator, progress

    def read(self, observation, place):
        # Modal evidence is checked before the underlying park toolbar.
        reward = self.nav.text(observation, '^GET!$', (530, 20, 230, 110))
        close = self.nav.text(observation, '^點擊關閉$', (480, 600, 350, 105)) if reward else []
        if len(reward) == len(close) == 1:
            return ParkView('reward', observation, tuple(close[0].box))
        scratch = self.nav.text(observation, '^點擊刮開[!！]$', (480, 565, 350, 85))
        if (place == LOTTERY_PLACE and len(scratch) == 1 and
                self.nav.vision.match(observation.image, 'lottery_scratch_card')):
            return ParkView('scratch', observation, tuple(scratch[0].box))
        if self.nav.vision.match(observation.image, 'park_dialogue_next'):
            lines = self.nav.text(observation, '.+', (200, 610, 850, 60))
            speaker = self.nav.text(observation, place.speaker or '.+', (550, 560, 170, 50))
            if len(speaker) == 1 and lines:
                signature = '|'.join(getattr(item, 'text', '').strip() for item in lines)
                return ParkView('dialogue', observation, signature=signature)
            return ParkView('unknown', observation)
        if observation.scene == 'home':
            labels = self.nav.text(observation, place.prompt, (860, 405, 370, 85))
            key = self.nav.vision.match(observation.image, 'park_interact_key')
            icon = (place == LOTTERY_PLACE or any(self.nav.vision.find_all(
                observation.image, name, roi=(850, 415, 110, 65))
                for name in ('park_gift_prompt', 'park_gift_prompt_compact')))
            if len(labels) == 1 and key and icon:
                return ParkView('entry', observation)
            if key:
                return ParkView('prompt_loading', observation)
            return ParkView('park', observation)
        return ParkView('unknown', observation)

    def entry(self, task, *, exclude=()):
        menu = self.nav.open_notifications()
        places = GIFT_PLACES if task == 'park_gifts' else (LOTTERY_PLACE,)
        matches = []
        for place in places:
            if place.key in exclude:
                continue
            items = self.nav.text(menu, place.notice, (20, 90, 490, 480))
            if len(items) > 1:
                raise NavigationError('樂園通知不唯一，未進行傳送')
            if len(items) == 1:
                matches.append((place, items[0]))
        if not matches:
            raise EntryUnavailable('沒有可確認的未領禮物／抽獎通知，略過本次任務')
        place, item = matches[0]
        x, y, w, h = item.box
        self.nav.inputs.click(int(x+w/2), int(y+h/2))
        self.nav.wait_for({'home'}, timeout=45)
        for segment in range(11):
            view = self.read(self.nav.observe(), place)
            if view.kind == 'entry':
                return place, view
            if view.kind == 'prompt_loading':
                self.nav.inputs.pause(.2)
                continue
            if view.kind != 'park':
                raise NavigationError('接近禮物／抽獎時畫面已改變，停止移動')
            if segment < 10:
                self.nav.inputs.key(0x57, .30)
                self.nav.inputs.pause(.1)
        self.nav.save(view.observation, place.key + '-not-reached')
        raise NavigationError('未在有限移動距離內找到禮物／刮刮樂互動提示')

    def consume(self, place, before_input=lambda: None):
        view = self.read(self.nav.observe(), place)
        if view.kind != 'entry':
            raise NavigationError('每日互動提示已改變，未消耗次數')
        # This exclusive, flushed record precedes the very first E. A crash or
        # uncertain outcome blocks another attempt in the same game day.
        attempt = self.nav.daily_attempts.reserve(place.key)
        try:
            before_input()
            self.nav.inputs.key(0x45, .12)
            evidence = self.finish_interaction(place)
            attempt.finish(evidence=evidence)
            return evidence
        except Exception as error:
            attempt.finish(error=error)
            raise

    def finish_interaction(self, place):
        deadline = time.monotonic()+70
        reward = None
        last_dialogue = None
        stable_since = None
        page_count = 0
        reward_closed = scratch_clicked = False
        park_since = None
        while time.monotonic() < deadline:
            view = self.read(self.nav.observe(), place)
            if view.kind not in ('park', 'entry'):
                park_since = None
            if view.kind == 'reward' and not reward_closed:
                reward = self.nav.save(view.observation, place.key + '-reward')
                self.click(view.button)
                reward_closed = True
                self.nav.inputs.pause(.5)
            elif view.kind == 'scratch' and not scratch_clicked:
                self.click(view.button)
                scratch_clicked = True
                self.nav.inputs.pause(.5)
            elif view.kind == 'dialogue':
                if view.signature != last_dialogue:
                    last_dialogue, stable_since = view.signature, time.monotonic()
                elif stable_since is not None and time.monotonic()-stable_since >= .6:
                    if page_count >= 8:
                        raise NavigationError('對話頁數超過錄影流程上限')
                    self.nav.inputs.key(0x45, .12)
                    page_count += 1
                    # No repeated E on the same line. Wait for a changed line,
                    # reward or park; a missing transition stops at the deadline.
                    stable_since = None
            elif view.kind in ('park', 'entry') and reward_closed:
                # Lottery keeps its interaction prompt after today's draw.
                # Never press E again; only verify the completed return.
                park_since = time.monotonic() if park_since is None else park_since
                if time.monotonic()-park_since >= .6:
                    return reward
            self.nav.inputs.pause(.2)
        self.nav.save(view.observation, place.key + '-stalled')
        raise NavigationError('每日互動未取得明確 GET 與返回樂園結果；保留本日嘗試紀錄')

    def click(self, box):
        x, y, w, h = box
        self.nav.inputs.click(int(x+w/2), int(y+h/2))

    def run(self, config):
        completed, excluded, evidence = 0, set(), None
        for _ in range(3 if config.task == 'park_gifts' else 1):
            try:
                place, view = self.entry(config.task, exclude=excluded)
            except EntryUnavailable:
                if completed:
                    return TaskResult(TaskStatus.COMPLETE, '已領取所有可確認的樂園通知項目', completed, evidence)
                raise
            evidence = self.nav.save(view.observation, place.key + '-entry')
            if config.entry_only:
                return TaskResult(TaskStatus.ENTRY_VERIFIED, '每日互動入口已驗證，未按 E 或消耗次數', evidence=evidence)
            excluded.add(place.key)
            # Preserve earlier completed places if a later place fails.
            self.progress(TaskResult(TaskStatus.RUNNING, '即將執行每日互動：'+place.key,
                                     completed, evidence))
            evidence = self.consume(place, lambda: self.progress(TaskResult(
                TaskStatus.RUNNING, '已記錄本日嘗試，即將按 E：'+place.key,
                completed, evidence, pending_round=True)))
            completed += 1
            self.progress(TaskResult(TaskStatus.RUNNING, '已確認每日領取結果', completed, evidence))
        return TaskResult(TaskStatus.COMPLETE, '已確認樂園每日領取結果', completed, evidence)
