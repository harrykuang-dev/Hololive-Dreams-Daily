"""Recorded notification teleports and once-daily gift/scratch interactions."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
import re
import time

import cv2

from .calendar import game_day
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
    ParkPlace('park_gift_the_cell', r'^可在The\s*Cell\s*重臨領取禮物$', r'禮物$'),
    ParkPlace('park_gift_rock_mountain', r'^可在.{1,3}岩山領取禮物$', r'禮物$'),
)
LOTTERY_PLACE = ParkPlace('lottery', r'^可遊玩洽馬洽馬極運輪盤$', r'^刮刮樂$')


class GiftMarkerMissing(NavigationError):
    pass


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
        if (observation.scene == 'park_dialogue'
                or self.nav.vision.match(observation.image, 'park_dialogue_next')):
            lines = self.nav.text(observation, '.+', (200, 610, 850, 60))
            speaker = self.nav.text(observation, place.speaker or '.+', (550, 560, 170, 50))
            signature = '|'.join(getattr(item, 'text', '').strip() for item in lines)
            if place == LOTTERY_PLACE and len(lines) == 1:
                intro = re.sub(r'[\s，,～~♪！!]+', '', signature)
                if intro == '洽馬的刮刮樂快來試試手氣吧' and len(speaker) <= 1:
                    return ParkView('dialogue', observation, signature=intro)
            # Some names are symbols, not OCR-readable words. The exact gift
            # handover line plus the observed dialogue-next control is enough
            # for this one continuation, never a new initial interaction.
            gift_line = (place in GIFT_PLACES and not speaker and
                         re.sub(r'[\s|，,！!。.]+', '', signature) == '這是我的一點小心意收下吧')
            if lines and (len(speaker) == 1 or gift_line):
                return ParkView('dialogue', observation, signature=signature)
            return ParkView('unknown', observation)
        if place in GIFT_PLACES and (signature := self.nav.gift_transfer_dialogue(observation)):
            return ParkView('dialogue', observation, signature=signature)
        if observation.scene == 'home':
            labels = self.nav.text(observation, place.prompt, (860, 405, 370, 85))
            key = self.nav.vision.match(observation.image, 'park_interact_key')
            if not key:
                # The key's glow changes its template pixels. Require the exact
                # E in its own small box instead of lowering template confidence.
                keys = [item for item in self.nav.text(observation, '^E$', (790, 410, 70, 65))
                        if getattr(item, 'text', '').strip() == 'E']
                key = len(keys) == 1
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
        self.wait_for_arrival(place)
        forward_steps = camera_steps = 0
        previous_offset = None
        previous_dx = None
        camera_gains = []
        for segment in range(27):
            view = self.read(self.nav.observe(), place)
            deadline = time.monotonic()+5
            while view.kind == 'unknown' and time.monotonic() < deadline:
                self.nav.inputs.pause(.2)
                view = self.read(self.nav.observe(), place)
            if view.kind == 'entry':
                return place, view
            if place.key == 'park_gift_holo_house' and view.kind in ('park', 'prompt_loading'):
                deadline = time.monotonic()+3
                while True:
                    try:
                        offset = self.house_gift_offset(view)
                        break
                    except GiftMarkerMissing:
                        if time.monotonic() >= deadline:
                            raise
                        self.nav.inputs.pause(.2)
                        view = self.read(self.nav.observe(), place)
                        if view.kind == 'entry':
                            return place, view
                        if view.kind not in ('park', 'prompt_loading'):
                            raise NavigationError('等待禮物標記時畫面已改變，停止移動')
                if previous_offset is not None and abs(offset) > abs(previous_offset)*1.1+5:
                    self.nav.save(view.observation, 'gift-camera-wrong-direction')
                    raise NavigationError('鏡頭移動未讓禮物標記接近中央，停止接近')
                if previous_offset is not None and previous_dx:
                    # Measure this client's actual camera response. A fixed
                    # proportional step can exhaust the bounded allowance
                    # even while every fresh marker moves toward the centre.
                    gain = (previous_offset-offset)/previous_dx
                    if .25 <= gain <= 8:
                        camera_gains.append(gain)
                previous_offset = None
                if abs(offset) > 35:
                    if camera_steps >= 12:
                        self.nav.save(view.observation, 'gift-camera-limit')
                        raise NavigationError('禮物鏡頭對準次數已達上限')
                    step = offset*.75/median(camera_gains[-3:]) if camera_gains else offset*.15
                    dx = max(-40, min(40, round(step)))
                    self.nav.inputs.look(dx)
                    self.nav.inputs.pause(.15)
                    previous_offset = offset
                    previous_dx = dx
                    camera_steps += 1
                    continue
            if view.kind == 'prompt_loading':
                self.nav.inputs.pause(.2)
                continue
            if view.kind != 'park':
                self.nav.save(view.observation, place.key + '-approach-unknown')
                raise NavigationError('接近禮物／抽獎時畫面已改變，停止移動')
            if forward_steps < 10:
                self.nav.inputs.key(0x57, .30)
                self.nav.inputs.pause(.1)
                forward_steps += 1
            else:
                break
        self.nav.save(view.observation, place.key + '-not-reached')
        raise NavigationError('未在有限移動距離內找到禮物／刮刮樂互動提示')

    def wait_for_arrival(self, place):
        deadline = time.monotonic() + 45
        saw_loading = False
        home_since = None
        while True:
            observation = self.nav.observe(park_loading_only=not saw_loading)
            if time.monotonic() >= deadline:
                self.nav.save(observation, f'{place.key}-arrival-unverified')
                raise NavigationError('未確認樂園傳送完成，停止接近')
            if observation.scene == 'park_teleport_loading':
                saw_loading = True
            if saw_loading and observation.scene == 'home':
                if home_since is None:
                    home_since = observation.captured_at
                elif observation.captured_at - home_since >= .35:
                    return
            else:
                home_since = None
            self.nav.inputs.pause(.2 if saw_loading else .1)

    def house_gift_offset(self, view):
        markers = self.nav.vision.find_all(view.observation.image, 'park_character_gift_marker',
                                            roi=(300, 100, 800, 350))
        if len(markers) != 1:
            self.nav.save(view.observation, 'gift-marker-not-unique')
            error = GiftMarkerMissing if not markers else NavigationError
            raise error('無法唯一確認角色頭頂的禮物標記，停止移動')
        x, y, w, h = markers[0].box
        if recording := getattr(self.nav, 'recording', None):
            recording.mark('gift_marker_verified', box=list(markers[0].box), score=markers[0].score)
        return x+w/2-640

    def consume(self, place, before_input=lambda: None):
        view = self.read(self.nav.observe(), place)
        deadline = time.monotonic()+3
        while view.kind in ('park', 'prompt_loading') and time.monotonic() < deadline:
            self.nav.inputs.pause(.2)
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

    def finish_interaction(self, place, *, advance_dialogue=True):
        deadline = time.monotonic()+70
        reward = None
        last_dialogue = None
        stable_since = None
        page_count = 0
        reward_closed = scratch_clicked = False
        park_since = None
        while time.monotonic() < deadline:
            view = self.read(self.nav.observe(), place)
            if view.kind not in ('park', 'entry', 'prompt_loading'):
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
            elif view.kind == 'dialogue' and advance_dialogue:
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
            elif view.kind in ('park', 'entry', 'prompt_loading') and reward_closed:
                # Lottery keeps its interaction prompt after today's draw.
                # Gifts can replace their gift label with the character name.
                # Only a verified dialogue page can advance after GET. A
                # free-movement prompt cannot start another interaction.
                park_since = time.monotonic() if park_since is None else park_since
                if time.monotonic()-park_since >= .6:
                    return reward
            self.nav.inputs.pause(.2)
        self.nav.save(view.observation, place.key + '-stalled')
        raise NavigationError('每日互動未取得明確 GET 與返回樂園結果；保留本日嘗試紀錄')

    def resume_house_gift(self):
        place = GIFT_PLACES[0]
        ledger = self.nav.daily_attempts
        recording = self.nav.recording
        reference = None

        def matches(observation):
            if (reference is None or observation.scene not in {'home', 'park_dialogue'}
                    or not self.nav.gift_transfer_dialogue(observation)
                    or observation.image.shape != reference.shape):
                return False
            # The emblem/nameplate and two fixed parts of the room distinguish
            # the original conversation from the same gift line elsewhere.
            for x, y, w, h in ((612, 573, 60, 21), (320, 75, 165, 80), (840, 125, 140, 105)):
                difference = cv2.absdiff(reference[y:y+h, x:x+w], observation.image[y:y+h, x:x+w])
                if max(cv2.mean(difference)[:3]) > 8:
                    return False
            return 0 <= time.monotonic() - observation.captured_at <= 2

        def verify(video, frames):
            nonlocal reference
            if recording is None or not recording.matches_window(video):
                return False
            capture = cv2.VideoCapture(str(video))
            try:
                if not capture.set(cv2.CAP_PROP_POS_FRAMES, frames - 1):
                    return False
                ok, frame = capture.read()
                if not ok or capture.get(cv2.CAP_PROP_POS_FRAMES) != frames:
                    return False
                reference = self.nav.vision.normalize(frame)
            finally:
                capture.release()
            return matches(self.nav.observe())

        attempt = ledger.resume_open_dialogue(place.key, verify_gift=verify)
        try:
            self.progress(TaskResult(TaskStatus.RUNNING, '接續原本的 Holo House 禮物對話', pending_round=True))
            current = self.nav.observe()
            if not matches(current):
                raise NavigationError('禮物對話已改變，未送出接續操作')

            def check_before_e():
                if (game_day(ledger.clock()) != attempt.record['game_day']
                        or not 0 <= time.monotonic() - current.captured_at <= 2):
                    raise NavigationError('禮物對話畫面已過期，未送出接續操作')

            self.nav.inputs.key(0x45, .12, before_post=check_before_e)
            evidence = self.finish_interaction(place, advance_dialogue=False)
            attempt.finish(evidence=evidence)
            return TaskResult(TaskStatus.COMPLETE, '已接續原禮物對話並確認結果', 1, evidence)
        except Exception as error:
            attempt.finish(error=error)
            raise

    def click(self, box):
        x, y, w, h = box
        self.nav.inputs.click(int(x+w/2), int(y+h/2))

    def run(self, config):
        if config.task == 'lottery' and not config.entry_only:
            view = self.read(self.nav.observe(), LOTTERY_PLACE)
            if (view.kind == 'dialogue' and '刮出什麼' in view.signature
                    and '玩一張看看' in view.signature):
                attempt = self.nav.daily_attempts.resume_open_dialogue('lottery')
                self.progress(TaskResult(TaskStatus.RUNNING, '接續本日已開啟的抽獎對話', pending_round=True))
                try:
                    evidence = self.finish_interaction(LOTTERY_PLACE)
                    attempt.finish(evidence=evidence)
                    return TaskResult(TaskStatus.COMPLETE, '已接續原抽獎對話並確認結果', 1, evidence)
                except Exception as error:
                    attempt.finish(error=error)
                    raise
        excluded = set() if config.entry_only else self.nav.daily_attempts.attempted_tasks()
        if (config.task == 'park_gifts' and GIFT_PLACES[0].key in excluded
                and self.nav.gift_transfer_dialogue(self.nav.observe())):
            return self.resume_house_gift()
        completed, evidence = 0, None
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
