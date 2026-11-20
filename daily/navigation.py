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
from .window_capture import game_in_foreground


class NavigationError(RuntimeError):
    pass


class EntryUnavailable(NavigationError):
    pass


class SessionDisplaced(RuntimeError):
    def __init__(self, evidence: str):
        super().__init__('遊戲帳號已在其他裝置登入，本輪已停止')
        self.evidence = evidence


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
        options = {'guard': self._check_game, 'trace': recording.mark} if recording else {}
        self.inputs = Inputs(self.controller, stopping=lambda: context.tasker.stopping, **options)
        self.vision = Vision(resources)
        self.evidence = evidence
        self._last_known_scene = None
        self._unknown_recovery_used = False
        evidence.mkdir(parents=True, exist_ok=True)

    def _check_game(self):
        self.recording.check()
        hwnd = getattr(self.recording, 'hwnd', None)
        if hwnd is not None and not game_in_foreground(hwnd):
            raise NavigationError('遊戲視窗已離開前景，停止操作')

    def observe(self, *, park_loading_only=False) -> Observation:
        self.inputs.check()
        job = self.controller.post_screencap().wait()
        if not job.succeeded:
            raise NavigationError('無法擷取遊戲畫面')
        self.inputs.check()
        captured = time.monotonic()
        observation = self.interpret(job.get(), captured_at=captured, park_loading_only=park_loading_only)
        if self.recording:
            self.recording.mark('observation', scene=observation.scene)
        if observation.scene == 'session_displaced':
            raise SessionDisplaced(self.save(observation, 'session-displaced'))
        if observation.scene is not None:
            self._last_known_scene = observation.scene
        return observation

    def interpret(self, image, *, captured_at=None, park_loading_only=False):
        captured = time.monotonic() if captured_at is None else captured_at
        frame = self.vision.normalize(image)
        if self.park_teleport_loading(frame):
            return Observation(frame, 'park_teleport_loading', captured)
        if park_loading_only:
            return Observation(frame, None, captured)
        scene = self.vision.scene(frame)
        observation = Observation(frame, scene, captured)
        if scene == 'song_select' and self.multiplayer_ready_controls(observation):
            # Both pages share song artwork. Ready's fixed controls take
            # precedence over the selection template's changing background.
            return Observation(frame, 'multi_ready', captured)
        if scene == 'fishing_map_second':
            # MAP's large shared header cannot distinguish the three areas.
            area = self.text(observation, '汽水泡泡灣', (65, 5, 350, 110))
            if not any('汽水泡泡灣' in getattr(item, 'text', '') for item in area):
                scene = 'park_map'
        if scene is None:
            # The park backdrop and notification badges can obscure labels.
            # The gamepad icon can stand in for its own unreadable label.
            toolbar = self.text(observation, r'^(遊戲|Live|任務|成就)$', (700, 15, 480, 100))
            words = {getattr(item, 'text', '').strip() for item in toolbar} & {'遊戲', 'Live', '任務', '成就'}
            if len(words) >= 3:
                scene = 'home'
            elif (len(words) == 2 and '遊戲' not in words
                    and self.vision.match(frame, 'home_games_button')):
                scene = 'home'
            else:
                if self.session_displaced_notice(observation):
                    return Observation(frame, 'session_displaced', captured)
                if self.game_update_button(observation):
                    return Observation(frame, 'game_update', captured)
                if self.fishing_failure_controls(observation):
                    return Observation(frame, 'fishing_failure', captured)
                if self.multiplayer_ready_controls(observation):
                    return Observation(frame, 'multi_ready', captured)
                if self.song_select_controls(observation):
                    return Observation(frame, 'song_select', captured)
                if self.event_home_controls(observation):
                    return Observation(frame, 'event_home', captured)
                if self.expired_conversion_button(observation):
                    return Observation(frame, 'expired_conversion', captured)
                if self.date_change_button(observation):
                    return Observation(frame, 'date_changed', captured)
                if self.login_bonus_button(observation):
                    return Observation(frame, 'login_bonus', captured)
                if self.standard_login_bonus_button(observation):
                    return Observation(frame, 'login_bonus_standard', captured)
                if self.login_reward_button(observation):
                    return Observation(frame, 'login_reward', captured)
                if self.login_notice_button(observation):
                    return Observation(frame, 'login_notice', captured)
                if self.live_rank_up_button(observation):
                    return Observation(frame, 'live_rank_up', captured)
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
        if scene == 'home' and (self.vision.match(frame, 'park_dialogue_next')
                                or self.park_dialogue_footer(observation)):
            scene = 'park_dialogue'
        return Observation(frame, scene, captured)

    def session_displaced_notice(self, observation):
        expected = r'^已[於于]其他裝置登入[，,]?即將返回標題畫面[。]?$'
        labels = self.text(observation, '.+', (390, 333, 500, 58))
        line = ''.join(item.text for item in sorted(labels, key=lambda item: item.box[0]))
        return bool(re.fullmatch(expected, re.sub(r'\s+', '', line)))

    def song_select_controls(self, observation):
        # The selected difficulty and the rating can change the old template.
        controls = (('搜尋', (648, 31, 111, 49)), ('譜面資訊', (979, 529, 116, 33)),
                    ('決定', (1060, 575, 185, 110)))
        for name, roi in controls:
            labels = self.text(observation, f'^{name}$', roi)
            if len(labels) != 1 or labels[0].text.strip() != name:
                return False
        return True

    @staticmethod
    def park_teleport_loading(frame):
        # Harmony Garden shows a green field, a sliding white castle, and
        # a peach chevron. Keep the logo search wide enough for its motion.
        field = frame[160:580, 135:415]
        green = cv2.inRange(field, (95, 108, 42), (120, 137, 65))
        if cv2.countNonZero(green) >= green.size * .98:
            logo = frame[160:452, 545:970]
            white = cv2.inRange(logo, (235, 235, 235), (255, 255, 255))
            chevron = cv2.inRange(frame[:, 900:1270], (148, 172, 218), (187, 205, 250))
            if (.08 <= cv2.countNonZero(white) / white.size <= .20
                    and cv2.countNonZero(chevron) >= chevron.size * .36):
                return True
        # The door animation can cover the centre and the upper band can be
        # entirely pale. Both borders keep the same blue and pale palette.
        blue_pixels = 0
        for band in (frame[15:75, 40:1240], frame[645:705, 40:1240]):
            blue = cv2.inRange(band, (235, 140, 24), (255, 204, 61))
            pale = cv2.inRange(band, (236, 229, 157), (255, 255, 255))
            if cv2.countNonZero(cv2.bitwise_or(blue, pale)) < blue.size * .88:
                return False
            blue_pixels += cv2.countNonZero(blue)
        return blue_pixels >= blue.size * .48

    def multiplayer_ready_controls(self, observation):
        heading = r'^(?:∴\s*)?難易度選擇中$'
        labels = [getattr(item, 'text', '').strip() for item in
                  self.text(observation, heading, (540, 5, 240, 55))]
        if sum(bool(re.fullmatch(heading, label)) for label in labels) != 1:
            return False
        auto = [item for item in self.text(observation, '^AUTO$', (125, 625, 90, 55))
                if getattr(item, 'text', '').strip() == 'AUTO']
        if len(auto) != 1:
            return False
        difficulty = self.text(observation, r'EASY|NORMAL|HARD|EXPERT', (700, 650, 325, 50))
        words = re.findall(r'\b(?:EASY|NORMAL|HARD|EXPERT)\b',
                           ' '.join(getattr(item, 'text', '') for item in difficulty))
        # EXPERT can fall below the OCR threshold. Require all other fixed
        # labels, without interpreting the Auto label as an enabled switch.
        return all(words.count(word) == 1 for word in ('EASY', 'NORMAL', 'HARD'))

    def fishing_failure_controls(self, observation):
        # A failed catch has a different heading from the success template.
        # Only recognize its fixed labels; never click Continue to recover it.
        groups = [
            (r'^失敗$', (870, 150, 220, 100)),
            (r'^魚[餌饵]被吃掉了[.…⋯]*$', (790, 245, 350, 70)),
            (r'^[×xX✕]?\s*取消$', (720, 620, 230, 80)),
            (r'^繼續$', (1000, 620, 200, 80)),
        ]
        for expected, roi in groups:
            labels = [item for item in self.text(observation, expected, roi)
                      if re.fullmatch(expected, getattr(item, 'text', '').strip())]
            if len(labels) != 1:
                return False
        return True

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
        bonus_clicks = 0
        login_flow = False
        reward_closed = False
        notice_closed = False
        achievement_closed = False
        game_entered = set()
        update_confirmed = False
        while True:
            observation = self.observe()
            recovery = None
            bonus_key = None
            if observation.scene in {'login_bonus', 'login_bonus_standard', 'login_reward', 'expired_conversion'}:
                bonus_key = self.login_page_key(observation)
            if 'home' in scenes:
                if observation.scene == 'date_changed' and not date_acknowledged:
                    recovery = self.date_change_button
                elif observation.scene == 'login_bonus' and bonus_key not in bonus_acknowledged:
                    recovery = self.login_bonus_button
                elif observation.scene == 'login_bonus_standard' and bonus_key not in bonus_acknowledged:
                    recovery = self.standard_login_bonus_button
                elif observation.scene == 'login_reward' and bonus_key not in bonus_acknowledged:
                    recovery = self.login_reward_button
                elif observation.scene == 'expired_conversion' and bonus_key not in bonus_acknowledged:
                    recovery = self.expired_conversion_button
                elif observation.scene is None and login_flow:
                    # After verified login/reset, an unfamiliar illustration
                    # may hide its title. Its exact continuation footer is
                    # sufficient here, but never in an arbitrary game scene.
                    key = ('startup_reward', self.login_page_key(observation))
                    if key not in bonus_acknowledged and self.login_continuation_button(observation):
                        bonus_key = key
                        recovery = self.login_continuation_button
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
                elif observation.scene == 'game_update' and not update_confirmed:
                    recovery = self.game_update_button
            if recovery is not None:
                if bonus_key is not None and bonus_clicks >= 12:
                    raise NavigationError('登入獎勵頁數已達上限，請檢查錄影')
                button = recovery(observation)
                if button is None:
                    raise NavigationError('換日／登入獎勵提示已改變，未確認其他對話框')
                x, y, w, h = button
                self.inputs.click(int(x+w/2), int(y+h/2))
                login_flow |= observation.scene in {'game_title_ready', 'date_changed'}
                date_acknowledged |= observation.scene == 'date_changed'
                if bonus_key is not None:
                    bonus_acknowledged.add(bonus_key)
                    bonus_clicks += 1
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
                elif observation.scene == 'game_update':
                    update_confirmed = True
                    game_entered.discard('game_title')
                deadline = time.monotonic() + (900 if observation.scene == 'game_update' else timeout)
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

    def event_home_controls(self, observation):
        # Reward badges overlap the old task-icon template. Verify independent
        # menu words instead of depending on badge state, artwork or UI colour.
        for expected, roi in (
            (r'^兌[換换]所$', (1160, 200, 115, 65)),
            (r'^活動排名$', (320, 590, 200, 60)),
            (r'^(?:[♫♪🎵]\s*)?Live$', (910, 605, 175, 70))):
            # Let Maa detect the word without interpreting an emoji character
            # class; Python then validates the complete, constrained label.
            capture = 'Live$' if roi[0] == 910 else expected
            hits = [item for item in self.text(observation, capture, roi)
                    if re.fullmatch(expected, getattr(item, 'text', '').strip())]
            if len(hits) != 1:
                return False
        headings = self.text(observation, r'^(遊玩方式|活動加成|众活動加成)$', (20, 20, 370, 65))
        words = {getattr(item, 'text', '').strip().removeprefix('众') for item in headings}
        return words == {'遊玩方式', '活動加成'}

    def date_change_button(self, observation):
        labels = [item for item in self.text(observation, '^日期已變更$', (450, 290, 390, 140))
                  if getattr(item, 'text', '').strip() == '日期已變更']
        if len(labels) != 1:
            return None
        buttons = [item for item in self.text(observation, '^OK$', (980, 600, 260, 105))
                   if getattr(item, 'text', '').strip() == 'OK']
        return tuple(buttons[0].box) if len(buttons) == 1 else None

    def expired_conversion_button(self, observation):
        expected = r'^超過期限\s*[，,]?\s*已轉換$'
        titles = [item for item in self.text(observation, expected, (520, 145, 250, 65))
                  if re.fullmatch(expected, getattr(item, 'text', '').strip())]
        if len(titles) != 1:
            return None
        expected = r'^[✓✔√]?\s*OK[!！]?$'
        buttons = [item for item in self.text(observation, expected, (780, 505, 205, 75))
                   if re.fullmatch(expected, getattr(item, 'text', '').strip())]
        return tuple(buttons[0].box) if len(buttons) == 1 else None

    def gift_transfer_dialogue(self, observation):
        lines = self.text(observation, '.+', (200, 610, 850, 60))
        signature = '|'.join(getattr(item, 'text', '').strip() for item in lines)
        if re.sub(r'[\s|，,！!。.]+', '', signature) != '這是我的一點小心意收下吧':
            return None
        keys = [item for item in self.text(observation, '^E$', (975, 665, 40, 45))
                if getattr(item, 'text', '').strip() == 'E']
        return signature if len(keys) == 1 else None

    def park_dialogue_footer(self, observation):
        if observation.image is None:
            return False
        footer = observation.image[665:710, 975:1015]
        blue = cv2.inRange(footer, (149, 90, 0), (255, 230, 110))
        # A 26px blue keycap with white lettering sits in this footer, away
        # from the ordinary interaction E in the centre-right of the park.
        centre = blue[11:37, 7:33]
        if cv2.countNonZero(centre) < .75 * centre.size or cv2.countNonZero(blue) > .55 * blue.size:
            return False
        keys = self.text(observation, '^E$', (975, 665, 40, 45))
        if sum(getattr(item, 'text', '').strip() == 'E' for item in keys) == 1:
            return True
        # The key lettering can be washed out at the start of this page.
        lines = self.text(observation, '.+', (200, 610, 850, 60))
        line = re.sub(r'[\W_]+', '', ''.join(item.text for item in lines)).upper()
        if line != 'BAUBAU哈啊好想玩個痛快':
            return False
        speakers = self.text(observation, '^Mococo$', (550, 560, 170, 50))
        return sum(item.text.strip() == 'Mococo' for item in speakers) == 1

    def login_bonus_button(self, observation):
        # This observed event login page is not a generic "continue" handler.
        titles = [item for item in self.text(observation, '^萬聖節$', (450, 140, 390, 160))
                  if getattr(item, 'text', '').strip() == '萬聖節']
        if len(titles) != 1:
            return None
        hints = [item for item in self.text(observation, '^點擊繼續$', (560, 625, 180, 50), only_rec=True)
                 if getattr(item, 'text', '').strip() == '點擊繼續']
        return tuple(hints[0].box) if len(hints) == 1 else None

    def login_page_key(self, observation):
        # Page text distinguishes consecutive rewards of the same layout;
        # animated illustrations must not re-arm an unchanged reward page.
        if observation.image is None:
            return observation.scene
        words = self.text(observation, '.+', (80, 70, 1120, 530))
        return (observation.scene, tuple(sorted(re.sub(r'\s+', '', item.text)
                                               for item in words)))

    def login_reward_button(self, observation):
        # Recognize the meaning of the page, never a character, illustration,
        # event name or colour. The footer authorizes a blank-area click.
        titles = self.text(observation, '.+', (60, 30, 1160, 510))
        words = ' '.join(getattr(item, 'text', '').strip() for item in titles)
        if not (re.search(r'LOGIN', words, re.I) and re.search(r'BONUS', words, re.I)
                or re.search(r'登[入錄]\s*(獎勵|禮)', words)):
            if observation is None or not self.login_reward_deadline(observation):
                return None
            # Some rewards have only illustrated lettering. The countdown in
            # the upper-right corner and the exact centered footer still agree.
            footer = (589, 627, 103, 30)
            hints = self.text(observation, '^點擊繼續$', footer, only_rec=True)
            return footer if len(hints) == 1 and hints[0].text.strip() == '點擊繼續' else None
        return self.login_continuation_button(observation)

    def login_reward_deadline(self, observation):
        expected = r'^[●◷Q]?\s*\d{4}/\d{1,2}/\d{1,2}\s*\d{1,2}:\d{2}$'
        labels = self.text(observation, expected, (970, 20, 292, 53))
        return len(labels) == 1 and bool(re.fullmatch(expected, labels[0].text.strip()))

    def login_continuation_button(self, observation):
        expected = r'^(?:點擊(?:繼續|空白處|空白區域|任意位置|畫面)(?:繼續|領取)?|TAP\s+TO\s+CONTINUE)$'
        hints = [item for item in self.text(observation, expected, (60, 550, 1160, 155))
                 if re.fullmatch(expected, getattr(item, 'text', '').strip(), re.I)]
        if not hints:
            # Animated decorative footers can evade text detection. Read only
            # the continuation strip, without depending on its illustration.
            hints = [item for item in self.text(observation, expected, (560, 625, 180, 50), only_rec=True)
                     if re.fullmatch(expected, getattr(item, 'text', '').strip(), re.I)]
        if len(hints) != 1:
            return None
        # The footer itself is a non-purchase blank-area continuation surface.
        return tuple(hints[0].box)

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

    def live_rank_up_button(self, observation):
        for pattern, roi in (
            (r'^RANK$', (370, 67, 290, 108)),
            (r'^UP$', (665, 67, 155, 108)),
            (r'^夢幻\s*Rank$', (545, 182, 190, 47)),
        ):
            hits = [item for item in self.text(observation, pattern, roi)
                    if re.fullmatch(pattern, getattr(item, 'text', '').strip())]
            if len(hits) != 1:
                return None
        buttons = [item for item in self.text(observation, '^點擊繼續$', (555, 630, 168, 55))
                   if getattr(item, 'text', '').strip() == '點擊繼續']
        return tuple(buttons[0].box) if len(buttons) == 1 else None

    def live_achievement_button(self, observation):
        titles = [item for item in self.text(observation, '^達成獎勵$', (500, 90, 280, 90))
                  if getattr(item, 'text', '').strip() == '達成獎勵']
        if len(titles) != 1:
            return None
        words = [re.sub(r'\s+', '', getattr(item, 'text', '')).casefold() for item in self.text(
            observation, r'^(HI-SCORE|最大[Cc]ombo|[Ff]ull\s*[Cc]ombo)$', (240, 180, 760, 340))]
        if len(words) != 3 or set(words) != {'hi-score', '最大combo', 'fullcombo'}:
            labels = (
                (r'^HI-SCORE$', (267, 207, 145, 50)),
                (r'^最大[Cc]ombo$', (690, 204, 160, 51)),
                (r'^[Ff]ull\s*[Cc]ombo$', (693, 429, 158, 51)),
            )
            if any(len(self.text(observation, pattern, roi)) != 1 for pattern, roi in labels):
                return None
        return (1015, 70, 60, 60)

    def game_update_button(self, observation):
        amount = r'[0-9]+(?:[.][0-9]+)?\s*[KkMmGg][Bb]'
        download = r'下[載载]\s*[:：]\s*'
        labels = self.text(observation, '.+', (535, 362, 210, 44))
        line = ''.join(item.text.strip() for item in sorted(labels, key=lambda item: item.box[0]))
        if not re.fullmatch(download + amount, line):
            return None
        checks = (
            (r'^將下載新增檔案$', (500, 312, 280, 57)),
            (r'^[×xX✕]?\s*取消$', (795, 630, 160, 58)),
            (r'^[✓✔√]?\s*OK$', (1035, 630, 147, 58)),
        )
        for pattern, roi in checks:
            hits = [item for item in self.text(observation, pattern, roi)
                    if re.fullmatch(pattern, getattr(item, 'text', '').strip())]
            if len(hits) != 1:
                return None
        return tuple(hits[0].box)

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

    def _probe_unknown_home_overlay(self, observation):
        """One blank-area click, only while recovering the park or login."""
        if (self._unknown_recovery_used or observation.scene is not None
                or self._last_known_scene not in {None, 'home', 'game_title', 'game_title_ready',
                                                  'date_changed', 'login_bonus',
                                                  'login_bonus_standard', 'login_reward', 'login_notice'}
                or observation.image is None or self.park_dialogue_footer(observation)
                or self.vision.match(observation.image, 'park_dialogue_next')):
            return observation
        gray = cv2.cvtColor(observation.image, cv2.COLOR_BGR2GRAY)
        x, y = 640, 697
        patch = gray[y-15:y+16, x-40:x+41]
        # Don't click on a dark/blank capture, text, or the edge of a control.
        if (cv2.meanStdDev(gray)[1].item() < 10 or cv2.meanStdDev(patch)[1].item() > 18
                or cv2.countNonZero(cv2.Canny(patch, 40, 100)) > .015 * patch.size
                or self.text(observation, '.+', (x-75, y-28, 150, 51))):
            return observation
        self._unknown_recovery_used = True
        self.save(observation, 'home-unknown-before-blank-click')
        self.inputs.click(x, y)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            self.inputs.pause(.2)
            observation = self.observe()
            if observation.scene is not None:
                break
        return observation

    def to_home(self):
        observation = self.observe()
        if observation.scene is None and (close := self.high_low_idle_table(observation)):
            x, y, w, h = close.box
            self.inputs.click(x+w//2, y+h//2)
            return self.wait_for({'home'})
        # The continuation footer on dated login rewards pulses in opacity.
        timeout = 12 if observation.scene is None and self.login_reward_deadline(observation) else 3
        deadline = time.monotonic()+timeout
        while observation.scene is None and time.monotonic() < deadline:
            self.inputs.pause(.2)
            observation = self.observe()
        if observation.scene is None:
            if close := self.high_low_idle_table(observation):
                x, y, w, h = close.box
                self.inputs.click(x+w//2, y+h//2)
                return self.wait_for({'home'})
            observation = self._probe_unknown_home_overlay(observation)
        if observation.scene in {'game_title', 'game_title_ready', 'game_update'}:
            return self.wait_for({'home'}, timeout=120)
        if observation.scene in {'date_changed', 'login_bonus', 'login_bonus_standard', 'login_reward', 'expired_conversion',
                                 'login_notice'}:
            return self.wait_for({'home'}, timeout=45)
        if observation.scene in {'live_achievement', 'live_rank_up'}:
            from .live import SoloLiveRunner
            observation = SoloLiveRunner(self).finish_result(observation)
        if observation.scene == 'holowork_get':
            observation = self.wait_for({'home', 'live_result', 'song_select', 'solo_setup',
                                         'holowork', 'daily_missions', 'achievements',
                                         'event_missions', 'gifts'}, timeout=45)
        if (observation.scene == 'park_dialogue' or self.vision.match(observation.image, 'park_dialogue_next')
                or (observation.scene == 'home' and self.gift_transfer_dialogue(observation))):
            raise NavigationError('角色對話尚未結束，未嘗試其他選單')
        if observation.scene == 'notifications':
            self.inputs.key(0x1B, .08)
            return self.wait_for({'home'}, timeout=45)
        if observation.scene in {'fishing_result', 'fishing_failure'}:
            return self.click_text(observation.scene, r'^[×xX✕]?\s*取消$', 'home', roi=(720, 620, 230, 80))
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
            if observation.scene in {'cooking_setup', 'jump_setup', 'gifts'}:
                # These pages can also close directly to the park.
                destinations.add('home')
            self.inputs.click(1223, 55)
            if observation.scene == 'gifts':
                try:
                    observation = self.wait_for(destinations, timeout=5)
                except NavigationError:
                    # The claim animation can swallow the first close. Retry
                    # only if a fresh observation still shows the gift box.
                    if self.observe().scene != 'gifts':
                        raise
                    self.inputs.click(1223, 55)
                    observation = self.wait_for(destinations)
            else:
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
        scene = self.observe().scene
        if scene == 'solo_setup':
            return self.wait_for({'solo_setup'})
        if scene != 'song_select':
            if scene != 'live_portal':
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
        observation = self.wait_for({'home'}, timeout=5)
        for _ in range(2):
            if observation.scene != 'home':
                break
            self.inputs.click(260, 54)  # Centre of the park's speaker control.
            deadline = time.monotonic() + 3
            while True:
                observation = self.observe()
                if observation.scene == 'notifications':
                    return self.wait_for({'notifications'}, timeout=5)
                if observation.scene not in ('home', None) or time.monotonic() >= deadline:
                    break
                self.inputs.pause(.2)
        self.save(observation, 'notifications-not-opened')
        raise NavigationError('未確認通知面板已開啟')

    def to_holowork(self):
        if self.observe().scene in {'holowork', 'holowork_get', 'holowork_started'}:
            return self.wait_for({'holowork', 'holowork_get', 'holowork_started'})
        self.open_notifications()
        deadline = time.monotonic()+8
        while True:
            menu = self.observe()
            if menu.scene != 'notifications':
                raise NavigationError('holoWork 通知頁已變更，未嘗試其他入口')
            buttons = self.text(menu, r'^(?:有)?[已巳]完成.*[Hh]olo\s*[Ww]ork', (20, 90, 500, 545))
            if len(buttons) == 1:
                x, y, w, h = buttons[0].box
                self.inputs.click(int(x+w/2), int(y+h/2))
                return self.wait_for({'holowork'}, timeout=30)
            if time.monotonic() >= deadline:
                self.save(menu, 'holowork-notification-not-found')
                raise EntryUnavailable('通知中沒有可唯一確認的 holoWork 收取入口')
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

    def high_low_idle_table(self, observation):
        if observation.image is None:
            return None
        close = self.vision.match(observation.image, 'high_low_close')
        if (close is not None
                and len(self.text(observation, r'^投[注註]並開始$', (510, 540, 260, 95))) == 1
                and len(self.text(observation, r'^投[注註]金額為\s*50\s*枚硬幣$', (460, 125, 360, 65))) == 1):
            return close
        return None

    def to_high_low_entry(self):
        # Prefer the daily notification when the game shows it. The map is a
        # verified fallback, and both routes stop at the exact interaction.
        current = self.observe()
        matches = self.text(current, r'^(?:可遊玩|可挑戰)?比大小[！!]?$',
                            (20, 90, 490, 545)) if current.scene == 'notifications' else []
        if len(matches) > 1:
            raise NavigationError('比大小通知不唯一，未進行傳送')
        if matches:
            x, y, w, h = matches[0].box
            self.inputs.click(int(x+w/2), int(y+h/2))
            # Avoid accepting the old park during a teleport fade.
            from .park import ParkPlace, ParkRunner
            ParkRunner(self).wait_for_arrival(ParkPlace('high_low', '', '^比大小$'))
            return self.approach('^比大小$')
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
        raise NavigationError('未找到可唯一確認的比大小遊戲桌')

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
