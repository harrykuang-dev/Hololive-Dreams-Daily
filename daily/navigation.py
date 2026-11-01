"""Observed scene transitions. Consuming actions are separated from navigation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
import time

import cv2

from .inputs import Inputs
from .vision import Match, Vision


class NavigationError(RuntimeError):
    pass


class EntryUnavailable(NavigationError):
    pass


@dataclass(frozen=True)
class Observation:
    image: object
    scene: str | None
    captured_at: float


class Navigator:
    def __init__(self, context, resources: Path, evidence: Path, *, recording=None):
        self.context = context
        self.controller = context.tasker.controller
        self.recording = recording
        options = {'guard': recording.check, 'trace': recording.mark} if recording else {}
        self.inputs = Inputs(self.controller, stopping=lambda: context.tasker.stopping, **options)
        self.vision = Vision(resources)
        self.evidence = evidence
        evidence.mkdir(parents=True, exist_ok=True)

    def observe(self) -> Observation:
        self.inputs.check()
        job = self.controller.post_screencap().wait()
        if not job.succeeded:
            raise NavigationError('無法擷取遊戲畫面')
        captured = time.monotonic()
        observation = self.interpret(job.get(), captured_at=captured)
        if self.recording:
            self.recording.mark('observation', scene=observation.scene)
        return observation

    def interpret(self, image, *, captured_at=None):
        captured = time.monotonic() if captured_at is None else captured_at
        frame = self.vision.normalize(image)
        scene = self.vision.scene(frame)
        observation = Observation(frame, scene, captured)
        if scene == 'fishing_map_second':
            # MAP's large shared header cannot distinguish the three areas.
            area = self.text(observation, '汽水泡泡灣', (65, 5, 350, 110))
            if not any('汽水泡泡灣' in getattr(item, 'text', '') for item in area):
                scene = 'park_map'
        if scene is None:
            # The park backdrop and notification badges change. Require three
            # independently read toolbar labels instead of weakening a template.
            toolbar = self.text(observation, r'^(遊戲|Live|任務|成就)$', (700, 15, 480, 100))
            words = {getattr(item, 'text', '').strip() for item in toolbar}
            if len(words.intersection({'遊戲', 'Live', '任務', '成就'})) >= 3:
                scene = 'home'
            else:
                if self.date_change_button(observation):
                    return Observation(frame, 'date_changed', captured)
                if self.login_bonus_button(observation):
                    return Observation(frame, 'login_bonus', captured)
                if self.standard_login_bonus_button(observation):
                    return Observation(frame, 'login_bonus_standard', captured)
                if self.login_notice_button(observation):
                    return Observation(frame, 'login_notice', captured)
                if self.live_achievement_button(observation):
                    return Observation(frame, 'live_achievement', captured)
                if self.game_title_button(observation):
                    return Observation(frame, 'game_title', captured)
                if self.game_title_ready_button(observation):
                    return Observation(frame, 'game_title_ready', captured)
                # GET overlays inherit the underlying page's colour. Require
                # both labels instead of matching a blue header on a red page.
                if self.received_reward_button(observation):
                    return Observation(frame, 'holowork_get', captured)
                work = [item for item in self.text(observation, r'^[Hh]olo[Ww]ork$', (90, 0, 270, 95))
                        if re.fullmatch(r'[Hh]olo[Ww]ork', getattr(item, 'text', '').strip())]
                menu = self.text(observation, r'^(個人檔案|會員資格|持有物|其他)$', (680, 110, 530, 440)) if not work else []
                menu_words = {getattr(item, 'text', '').strip() for item in menu}
                extra = self.text(observation, r'^(標題畫面|結束遊戲|客服諮詢)$', (150, 250, 950, 220)) if not work and len(menu_words) < 3 else []
                extra_words = {getattr(item, 'text', '').strip() for item in extra}
                heading = self.text(observation, r'^MAP$', (65, 5, 240, 95)) if not work and len(menu_words) < 3 else []
                if len(work) == 1:
                    scene = 'holowork'
                elif len(menu_words.intersection({'個人檔案', '會員資格', '持有物', '其他'})) >= 3:
                    scene = 'main_menu'
                elif len(extra_words.intersection({'標題畫面', '結束遊戲', '客服諮詢'})) == 3:
                    scene = 'other_menu'
                elif any(getattr(item, 'text', '').strip() == 'MAP' for item in heading):
                    area = self.text(observation, '汽水泡泡灣', (65, 5, 350, 110))
                    scene = 'fishing_map_second' if any('汽水泡泡灣' in getattr(item, 'text', '')
                                                       for item in area) else 'park_map'
        return Observation(frame, scene, captured)

    def save(self, observation: Observation, name: str) -> str:
        path = self.evidence / f'{name}-{time.time_ns()}.png'
        if not cv2.imwrite(str(path), observation.image):
            raise OSError('Cannot save scene evidence')
        if self.recording:
            self.recording.mark('evidence', scene=observation.scene, path=str(path))
        return str(path)

    def wait_for(self, scenes, *, timeout=30, stable_for=.35) -> Observation:
        deadline = time.monotonic() + timeout
        stable_since = None
        last_scene = None
        date_acknowledged = False
        bonus_acknowledged = set()
        reward_closed = False
        notice_closed = False
        achievement_closed = False
        game_entered = set()
        while True:
            observation = self.observe()
            recovery = None
            if 'home' in scenes:
                if observation.scene == 'date_changed' and not date_acknowledged:
                    recovery = self.date_change_button
                elif observation.scene == 'login_bonus' and observation.scene not in bonus_acknowledged:
                    recovery = self.login_bonus_button
                elif observation.scene == 'login_bonus_standard' and observation.scene not in bonus_acknowledged:
                    recovery = self.standard_login_bonus_button
                elif observation.scene == 'holowork_get' and not reward_closed:
                    recovery = self.received_reward_button
                elif observation.scene == 'login_notice' and not notice_closed:
                    recovery = self.login_notice_button
                elif observation.scene == 'live_achievement' and not achievement_closed:
                    recovery = self.live_achievement_button
                elif observation.scene == 'game_title' and observation.scene not in game_entered:
                    recovery = self.game_title_button
                elif observation.scene == 'game_title_ready' and observation.scene not in game_entered:
                    recovery = self.game_title_ready_button
            if recovery is not None:
                button = recovery(observation)
                if button is None:
                    raise NavigationError('換日／登入獎勵提示已改變，未確認其他對話框')
                x, y, w, h = button
                self.inputs.click(int(x+w/2), int(y+h/2))
                date_acknowledged |= observation.scene == 'date_changed'
                if observation.scene in {'login_bonus', 'login_bonus_standard'}:
                    bonus_acknowledged.add(observation.scene)
                    reward_closed = False
                elif observation.scene == 'holowork_get':
                    reward_closed = True
                elif observation.scene == 'login_notice':
                    notice_closed = True
                elif observation.scene == 'live_achievement':
                    achievement_closed = True
                    reward_closed = False
                elif observation.scene in {'game_title', 'game_title_ready'}:
                    game_entered.add(observation.scene)
                deadline = time.monotonic() + timeout
                stable_since = last_scene = None
                continue
            if observation.scene in scenes:
                if observation.scene != last_scene:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= stable_for:
                    return observation
                last_scene = observation.scene
            else:
                stable_since = None
                last_scene = None
            if time.monotonic() >= deadline:
                evidence = self.save(observation, 'unknown')
                raise NavigationError(f'等待畫面逾時：{", ".join(scenes)}；{evidence}')
            time.sleep(.2)

    def date_change_button(self, observation):
        labels = [item for item in self.text(observation, '^日期已變更$', (450, 290, 390, 140))
                  if getattr(item, 'text', '').strip() == '日期已變更']
        if len(labels) != 1:
            return None
        buttons = [item for item in self.text(observation, '^OK$', (980, 600, 260, 105))
                   if getattr(item, 'text', '').strip() == 'OK']
        return tuple(buttons[0].box) if len(buttons) == 1 else None

    def login_bonus_button(self, observation):
        # This observed event login page is not a generic "continue" handler.
        titles = [item for item in self.text(observation, '^萬聖節$', (450, 140, 390, 160))
                  if getattr(item, 'text', '').strip() == '萬聖節']
        if len(titles) != 1:
            return None
        hints = [item for item in self.text(observation, '^點擊繼續$', (560, 625, 180, 50), only_rec=True)
                 if getattr(item, 'text', '').strip() == '點擊繼續']
        return tuple(hints[0].box) if len(hints) == 1 else None

    def received_reward_button(self, observation):
        titles = [item for item in self.text(observation, '^GET!$', (530, 20, 230, 110))
                  if getattr(item, 'text', '').strip() == 'GET!']
        if len(titles) != 1:
            return None
        hints = [item for item in self.text(observation, '^點擊關閉$', (480, 600, 350, 105))
                 if getattr(item, 'text', '').strip() == '點擊關閉']
        return tuple(hints[0].box) if len(hints) == 1 else None

    def standard_login_bonus_button(self, observation):
        titles = [getattr(item, 'text', '').strip() for item in self.text(
            observation, r'^(LOGIN|BONUS)$', (680, 100, 440, 180))
            if getattr(item, 'text', '').strip() in {'LOGIN', 'BONUS'}]
        if len(titles) != 2 or set(titles) != {'LOGIN', 'BONUS'}:
            return None
        hints = [item for item in self.text(observation, '^點擊繼續$', (800, 580, 200, 55))
                 if getattr(item, 'text', '').strip() == '點擊繼續']
        return tuple(hints[0].box) if len(hints) == 1 else None

    def login_notice_button(self, observation):
        # Only the recorded November membership announcement has this layout.
        # Close its top-right X; never enter the advertised pass or membership.
        required = (
            (r'^會員資格[/／]會員$', (200, 90, 460, 150), False),
            ('^豪華任務通行證$', (620, 120, 460, 120), False),
            (r'^2026年11月更新[!！]$', (320, 495, 640, 130), True))
        for expected, roi, only_rec in required:
            matches = [item for item in self.text(observation, expected, roi, only_rec=only_rec)
                       if re.fullmatch(expected, getattr(item, 'text', '').strip())]
            if len(matches) != 1:
                return None
        return (1060, 85, 60, 60)

    def live_achievement_button(self, observation):
        titles = [item for item in self.text(observation, '^達成獎勵$', (500, 90, 280, 90))
                  if getattr(item, 'text', '').strip() == '達成獎勵']
        if len(titles) != 1:
            return None
        words = [re.sub(r'\s+', '', getattr(item, 'text', '')).casefold() for item in self.text(
            observation, r'^(HI-SCORE|最大[Cc]ombo|[Ff]ull\s*[Cc]ombo)$', (240, 180, 760, 340))]
        if len(words) != 3 or set(words) != {'hi-score', '最大combo', 'fullcombo'}:
            return None
        return (1015, 70, 60, 60)

    def game_title_button(self, observation, *, ready=False):
        # Ordinary entry to the already-open Steam game, never a credentials or
        # account-selection dialog. Background animation is not an anchor.
        versions = [item for item in self.text(observation, r'^ver[.]\s*[0-9]+(?:[.][0-9]+){2}$', (10, 10, 210, 65))
                    if re.fullmatch(r'ver[.]\s*[0-9]+(?:[.][0-9]+){2}', getattr(item, 'text', '').strip())]
        if len(versions) != 1:
            return None
        engine = [item for item in self.text(observation, '^CRIWARE$', (1000, 10, 120, 95))
                  if getattr(item, 'text', '').strip() == 'CRIWARE']
        if len(engine) != 1:
            return None
        expected = r'^TAP\s+TO\s+START$' if ready else '^登入$'
        roi = (520, 630, 240, 70) if ready else (540, 620, 200, 90)
        buttons = [item for item in self.text(observation, expected, roi)
                   if re.fullmatch(expected, getattr(item, 'text', '').strip())]
        return tuple(buttons[0].box) if len(buttons) == 1 else None

    def game_title_ready_button(self, observation):
        return self.game_title_button(observation, ready=True)

    def text(self, observation: Observation, expected: str, roi=None, *, only_rec=False):
        # This is recognition only. The OCR pipeline has no input action.
        node = {'recognition': 'OCR', 'expected': [expected], 'threshold': .75,
                'only_rec': only_rec}
        if roi is not None:
            node['roi'] = list(roi)
        detail = self.context.run_recognition('DailyOCR', observation.image,
                                              {'DailyOCR': node})
        return list(detail.filtered_results) if detail and detail.hit else []

    def click_text(self, source: str, text: str, destination: str, *, roi=None):
        observation = self.wait_for({source}, timeout=5)
        matches = self.text(observation, text, roi)
        if len(matches) != 1:
            raise NavigationError(f'無法唯一確認按鈕：{text}')
        x, y, w, h = matches[0].box
        self.inputs.click(int(x+w/2), int(y+h/2))
        return self.wait_for({destination})

    def to_home(self):
        observation = self.observe()
        deadline = time.monotonic()+3
        while observation.scene is None and time.monotonic() < deadline:
            self.inputs.pause(.2)
            observation = self.observe()
        if observation.scene in {'game_title', 'game_title_ready'}:
            return self.wait_for({'home'}, timeout=120)
        if observation.scene in {'date_changed', 'login_bonus', 'login_bonus_standard',
                                 'login_notice'}:
            return self.wait_for({'home'}, timeout=45)
        if observation.scene in {'holowork_get', 'live_achievement'}:
            observation = self.wait_for({'home', 'live_result', 'song_select', 'solo_setup',
                                         'holowork', 'daily_missions', 'achievements',
                                         'event_missions', 'gifts'}, timeout=45)
        if self.vision.match(observation.image, 'park_dialogue_next'):
            raise NavigationError('角色對話尚未結束，未嘗試其他選單')
        if observation.scene == 'notifications':
            self.inputs.key(0x1B, .08)
            return self.wait_for({'home'}, timeout=45)
        if observation.scene == 'fishing_result':
            return self.click_text('fishing_result', r'^[×xX✕]?\s*取消$', 'home', roi=(720, 620, 230, 80))
        if observation.scene == 'other_menu':
            observation = self.click_text('other_menu', '取消$', 'main_menu', roi=(990, 610, 240, 100))
        if observation.scene == 'live_result':
            # Recover a result left open by an earlier run without starting or
            # counting another song. This path only advances reward pages.
            from .live import SoloLiveRunner
            observation = SoloLiveRunner(self).finish_result(observation)
        if observation.scene == 'jump_result':
            from .jump import JumpRunner
            observation = JumpRunner(self).finish_result()
        if observation.scene == 'cooking_result':
            from .minigames import MinigameResults
            observation = MinigameResults(self, 'cooking').finish_result()
        if observation.scene == 'energy_settings':
            observation = self.cancel_energy_settings()
        for _ in range(4):
            destination = {'cooking_setup': 'minigame_portal', 'jump_setup': 'minigame_portal',
                           'gifts': 'main_menu',
                           'multi_portal': 'live_portal',
                           'event_missions': 'event_home', 'event_home': 'event_list',
                           'solo_setup': 'song_select', 'song_select': 'live_portal'}.get(observation.scene)
            if destination is None:
                break
            destinations = {destination}
            if observation.scene in {'cooking_setup', 'jump_setup'}:
                # Minigames can return directly to the park after a round.
                # The portal is not their only observed parent destination.
                destinations.add('home')
            self.inputs.click(1223, 55)
            observation = self.wait_for(destinations)
        if observation.scene == 'home':
            return observation
        if observation.scene in {'park_map', 'fishing_map_second'}:
            # A modal information card disables the background X and M key.
            # Close its observed X before closing the map itself.
            card_close = self.vision.match(observation.image, 'map_card_close')
            if card_close:
                x, y, w, h = card_close.box
                self.inputs.click(x+w//2, y+h//2)
                deadline = time.monotonic()+5
                while self.vision.match(self.observe().image, 'map_card_close'):
                    if time.monotonic() >= deadline:
                        raise NavigationError('地圖資訊卡尚未關閉')
                    self.inputs.pause(.15)
            self.inputs.click(1223, 55)
            return self.wait_for({'home'})
        # These screens have the visibly recorded close button at top right.
        if observation.scene in {'live_portal', 'minigame_portal', 'daily_missions',
                                 'achievements', 'event_missions', 'gifts',
                                 'event_list',
                                 'fishing_setup_second', 'main_menu', 'holowork'}:
            self.inputs.click(1223, 55)
            return self.wait_for({'home'})
        evidence = self.save(observation, 'home-unrecognized')
        raise NavigationError(f'目前不在可安全返回樂園的畫面；{evidence}')

    def to_live_portal(self):
        self.to_home()
        return self.click_text('home', '^Live$', 'live_portal', roi=(730, 10, 380, 100))

    def to_multi_portal(self):
        if self.observe().scene == 'multi_portal':
            return self.wait_for({'multi_portal'}, timeout=5)
        self.to_live_portal()
        return self.click_text('live_portal', '^MULTI$', 'multi_portal', roi=(950, 140, 250, 340))

    def to_minigame_portal(self):
        self.to_home()
        observation = self.wait_for({'home'})
        labels = self.text(observation, '^遊戲$', roi=(730, 10, 380, 100))
        if len(labels) == 1:
            x, y, w, h = labels[0].box
        elif not labels:
            # Park scenery can make the small label unreadable. The solid
            # gamepad glyph is independent of the changing backdrop.
            button = self.vision.match(observation.image, 'home_games_button')
            if button is None:
                raise NavigationError('無法確認樂園的遊戲按鈕')
            x, y, w, h = button.box
        else:
            raise NavigationError('無法唯一確認樂園的遊戲按鈕')
        self.inputs.click(int(x+w/2), int(y+h/2))
        return self.wait_for({'minigame_portal'})

    def to_solo_setup(self):
        if self.observe().scene == 'solo_setup':
            return self.wait_for({'solo_setup'})
        self.to_live_portal()
        self.click_text('live_portal', '^SOLO$', 'song_select', roi=(630, 140, 250, 340))
        return self.click_text('song_select', '^決定$', 'solo_setup', roi=(1060, 575, 185, 110))

    def to_minigame_setup(self, game):
        title, destination = {'cooking': ('^食材[妙炒]廚房$', 'cooking_setup'),
                              'jump_rope': ('^蹦蹦大跳繩$', 'jump_setup')}[game]
        if self.observe().scene == destination:
            return self.wait_for({destination})
        portal = self.to_minigame_portal()
        labels = self.text(portal, title, roi=(20, 470, 1180, 100))
        if len(labels) != 1:
            raise NavigationError('無法唯一確認小遊戲卡片')
        x, y, w, h = labels[0].box
        left = max(0, int(x+w/2)-120)
        buttons = self.vision.find_all(portal.image, 'portal_go', roi=(left, 570, 240, 85))
        if len(buttons) != 1:
            raise NavigationError('無法確認所選小遊戲的 Go 按鈕')
        x, y, w, h = buttons[0].box
        self.inputs.click(int(x+w/2), int(y+h/2))
        return self.wait_for({destination}, timeout=45)

    def open_energy_settings(self, task):
        scene = {'solo_live': 'solo_setup', 'cooking': 'cooking_setup', 'jump_rope': 'jump_setup'}[task]
        observation = self.wait_for({scene}, timeout=5)
        anchor = 'solo_boost_button' if task == 'solo_live' else 'minigame_boost_button'
        button = self.vision.match(observation.image, anchor)
        if button is None:
            raise NavigationError('無法確認能量設定按鈕')
        x, y, w, h = button.box
        self.inputs.click(x+w//2, y+h//2)
        observation = self.wait_for({'energy_settings'}, timeout=8)
        self.save(observation, task+'-energy-settings')
        return observation

    def cancel_energy_settings(self):
        observation = self.wait_for({'energy_settings'}, timeout=5)
        buttons = self.text(observation, '取消$', roi=(730, 625, 230, 75))
        if len(buttons) != 1:
            raise NavigationError('無法確認能量設定的取消按鈕')
        x, y, w, h = buttons[0].box
        self.inputs.click(int(x+w/2), int(y+h/2))
        return self.wait_for({'solo_setup', 'cooking_setup', 'jump_setup'})

    def to_daily_missions(self):
        self.to_home()
        return self.click_text('home', '^任務$', 'daily_missions', roi=(730, 10, 400, 110))

    def open_notifications(self):
        if self.observe().scene == 'notifications':
            return self.wait_for({'notifications'}, timeout=5)
        self.to_home()
        self.wait_for({'home'}, timeout=5)
        self.inputs.click(260, 45)
        return self.wait_for({'notifications'})

    def to_holowork(self):
        if self.observe().scene in {'holowork', 'holowork_get', 'holowork_started'}:
            return self.wait_for({'holowork', 'holowork_get', 'holowork_started'})
        self.to_home()
        self.inputs.click(260, 45)  # Recorded notification speaker shortcut.
        self.wait_for({'notifications'}, timeout=8)
        deadline = time.monotonic()+8
        while True:
            menu = self.observe()
            if menu.scene != 'notifications':
                raise NavigationError('holoWork 通知頁已變更，未嘗試其他入口')
            buttons = self.text(menu, '[已巳]完成.*[Hh]olo[Ww]ork', (20, 90, 470, 480))
            if len(buttons) == 1:
                x, y, w, h = buttons[0].box
                self.inputs.click(int(x+w/2), int(y+h/2))
                return self.wait_for({'holowork'}, timeout=30)
            if time.monotonic() >= deadline:
                self.save(menu, 'holowork-notification-not-found')
                raise EntryUnavailable('沒有可唯一確認的已完成 holoWork 通知，略過本次收取')
            self.inputs.pause(.2)

    def to_fishing_second(self):
        if self.observe().scene == 'fishing_setup_second':
            return self.wait_for({'fishing_setup_second'}, timeout=5)
        self.to_home()
        # The recording uses M, not a click on the small minimap. Selecting a
        # fishing icon opens an information card; Move is a separate action.
        map_view = self.open_map()
        self.save(map_view, 'map-open')
        map_view = self.center_fishing_map(map_view)
        self.save(map_view, 'lagoon-map')
        hooks = self.wait_for_hooks(map_view)
        map_view = self.observe()
        moved = False
        # Reopen and normalize the overview after a wrong information card;
        # selecting a card pans the map, invalidating earlier coordinates.
        for index in range(min(4, len(hooks))):
            if index:
                self.to_home()
                map_view = self.open_map()
                map_view = self.center_fishing_map(map_view)
                hooks = self.wait_for_hooks(map_view)
            if index >= len(hooks): break
            x, y, w, h = hooks[index].box
            self.inputs.click(x+w//2, y+h//2)
            card = self.wait_for_map_card()
            self.save(card, f'fishing-card-{index}')
            title = self.text(card, '菈米的冰天雪堡', (780, 120, 450, 180))
            if not title:
                continue
            moves = self.text(card, '^移動$', (780, 400, 450, 240))
            if len(moves) != 1:
                raise NavigationError('已找到第二釣點，但無法確認移動按鈕')
            bx, by, bw, bh = moves[0].box
            self.inputs.click(bx+bw//2, by+bh//2)
            moved = True
            break
        if not moved:
            raise NavigationError('未找到菈米的冰天雪堡釣點')
        home = self.wait_for({'home'}, timeout=45)
        self.save(home, 'fishing-arrival')
        self.approach('開始釣魚')
        self.inputs.key(0x45, .12)  # E, from the recorded interaction prompt.
        return self.wait_for({'fishing_setup_second'})

    def to_high_low_entry(self):
        # Teleport only after reading the actual card title. Opening the game
        # with E is a separate once-daily boundary and is not enabled here.
        self.to_home()
        for index in range(4):
            if index:
                self.to_home()
            map_view = self.open_map()
            if not self.text(map_view, '電玩巨蛋城', (65, 5, 350, 110)):
                self.inputs.click(75, 530)  # Recorded middle region selector.
                deadline = time.monotonic()+6
                while True:
                    map_view = self.observe()
                    if (map_view.scene == 'park_map' and
                            self.text(map_view, '電玩巨蛋城', (65, 5, 350, 110))):
                        break
                    if time.monotonic() >= deadline:
                        raise NavigationError('無法確認電玩巨蛋城地圖，未選取互動點')
                    self.inputs.pause(.2)
            tabs = self.text(map_view, '^遊玩區域$', (150, 620, 330, 80))
            if len(tabs) != 1:
                raise NavigationError('無法唯一確認電玩巨蛋城的遊玩區域分頁')
            x, y, w, h = tabs[0].box
            self.inputs.click(int(x+w/2), int(y+h/2))
            self.wait_for({'park_map'}, timeout=6)
            self.inputs.click(994, 668)
            self.inputs.pause(.35)
            map_view = self.wait_for({'park_map'}, timeout=6)
            candidates = self.vision.find_all(map_view.image, 'map_game_portal', roi=(150, 95, 1030, 530))
            if index >= len(candidates):
                self.save(map_view, 'arena-no-game-candidate')
                break
            x, y, w, h = candidates[index].box
            self.inputs.click(x+w//2, y+h//2)
            deadline = time.monotonic()+6
            while True:
                card = self.observe()
                if card.scene == 'park_map' and self.vision.match(card.image, 'map_card_close'):
                    break
                if time.monotonic() >= deadline:
                    raise NavigationError('遊戲地圖資訊卡尚未顯示')
                self.inputs.pause(.2)
            self.save(card, 'high-low-map-card')
            title = self.text(card, '^比大小$', (780, 180, 450, 170))
            description = self.text(card, r'^Shiori的卡牌遊戲桌$', (810, 400, 390, 90))
            if len(title) != 1 or len(description) != 1:
                continue
            moves = self.text(card, '^移動$', (780, 480, 450, 100))
            if len(moves) != 1:
                raise NavigationError('比大小資訊卡的移動按鈕不唯一')
            x, y, w, h = moves[0].box
            self.inputs.click(int(x+w/2), int(y+h/2))
            self.wait_for({'home'}, timeout=45)
            return self.approach('^比大小$')
        raise NavigationError('未找到可唯一確認的 Shiori 比大小遊戲桌')

    def wait_for_map_card(self):
        deadline = time.monotonic()+6
        while True:
            card = self.observe()
            if (self.vision.match(card.image, 'map_card_close') and
                    self.text(card, '^釣魚場$', (780, 180, 450, 180))):
                return card
            if time.monotonic() >= deadline:
                self.save(card, 'map-card-not-open')
                raise NavigationError('釣點資訊卡尚未顯示')
            self.inputs.pause(.2)

    def center_fishing_map(self, observation):
        # M centers on the player and each region preserves its last viewport.
        # Use the visible zoom slider's minimum before looking for hooks, so the
        # northern site cannot remain hidden behind the map header/counter.
        if observation.scene == 'park_map':
            self.inputs.click(75, 610)
            self.wait_for({'fishing_map_second'})
        self.inputs.click(994, 668)
        self.inputs.pause(.35)
        return self.wait_for({'fishing_map_second'})

    def approach(self, interaction: str, *, segments=10):
        for index in range(segments+1):
            observation = self.observe()
            if observation.scene != 'home':
                raise NavigationError('接近互動點時畫面已改變，停止移動')
            matches = self.text(observation, interaction, roi=(700, 300, 560, 310))
            if len(matches) == 1:
                return observation
            if len(matches) > 1:
                raise NavigationError('互動提示不唯一，停止移動')
            if index < segments:
                self.inputs.key(0x57, .30)  # Bounded W segments from the recording.
                self.inputs.pause(.1)
        self.save(observation, 'interaction-not-reached')
        raise NavigationError(f'未在有限移動距離內找到互動提示：{interaction}')

    def open_map(self):
        for attempt in range(2):
            self.wait_for({'home'}, timeout=5)
            self.inputs.key(0x4D, .18)
            try:
                return self.wait_for({'park_map', 'fishing_map_second'}, timeout=6)
            except NavigationError:
                if attempt or self.observe().scene != 'home':
                    raise
        raise NavigationError('無法開啟地圖')

    def wait_for_hooks(self, observation: Observation, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            hooks = self.fishing_candidates(observation.image)
            if observation.scene == 'fishing_map_second' and hooks:
                return hooks
            if time.monotonic() >= deadline:
                self.save(observation, 'missing-map-hooks')
                raise NavigationError('地圖中找不到釣魚圖示')
            self.inputs.pause(.2)
            observation = self.observe()

    def fishing_candidates(self, image):
        hooks = self.vision.find_all(image, 'map_fishing_hook', roi=(150, 100, 1020, 590))
        # The player's marker covers the hook at their current fishing site.
        # Locate that destination from two independent, fixed map landmarks.
        # A candidate is never a teleport authorization: the information card's
        # exact place name and Move button are still required before moving.
        clock = self.vision.find_all(image, 'map_clock', roi=(150, 90, 1020, 560))
        shop = self.vision.find_all(image, 'map_shop', roi=(150, 90, 1020, 560))
        if len(clock) == len(shop) == 1:
            def center(box):
                x, y, w, h = box
                return x+w/2, y+h/2
            ca, sa = (center(self.vision.anchors[name]['roi']) for name in ('map_clock', 'map_shop'))
            c, s = center(clock[0].box), center(shop[0].box)
            sx, sy = (s[0]-c[0])/(sa[0]-ca[0]), (s[1]-c[1])/(sa[1]-ca[1])
            if .3 <= sx <= 2 and .3 <= sy <= 2 and abs(sx-sy) <= .12:
                target = center(self.vision.anchors['map_fishing_hook']['roi'])
                x, y = c[0]+sx*(target[0]-ca[0]), c[1]+sy*(target[1]-ca[1])
                if 160 <= x <= 1150 and 110 <= y <= 650:
                    estimate = Match('map_lami_candidate', min(clock[0].score, shop[0].score),
                                     (round(x)-4, round(y)-4, 8, 8))
                    hooks = [estimate]+[h for h in hooks if math.dist(center(h.box), (x,y)) > 30]
        return hooks
