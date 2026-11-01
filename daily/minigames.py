"""Fresh resource authorization for isolated minigame workers; never Live."""
from dataclasses import asdict
import time

from .navigation import NavigationError
from .resources import ResourceUnavailable, parse_fraction, read_one, read_setup_cost


def start_minigame(nav, task, status, energy, budget, before_input=lambda: None):
    scene = {'jump_rope': 'jump_setup', 'cooking': 'cooking_setup'}.get(task)
    if scene is None:
        raise NavigationError('此任務不支援小遊戲資源授權')
    if status.get('phase') != 'capture_ready':
        raise NavigationError('小遊戲模組尚未確認擷取就緒')
    observation = nav.observe()
    if observation.scene != scene:
        raise NavigationError('小遊戲開始前畫面不符')
    buttons = nav.text(observation, '^遊玩$', (980, 610, 260, 85))
    resource = parse_fraction(read_one(nav, observation, '[0-9].*[/／].*[0-9]', (1025, 25, 145, 60)))
    cost = read_setup_cost(nav, observation)
    if len(buttons) != 1:
        raise NavigationError('無法唯一確認小遊戲遊玩按鈕')
    explicit_free = energy.multiplier == 0 and energy.cost == 0
    if (cost != energy.effective_cost or not resource.permits(cost, budget)
            or (cost == 0 and resource.available != 0 and not explicit_free)):
        raise ResourceUnavailable('小遊戲實際消耗或本次資源上限不符，未開始遊玩')
    if not 0 <= time.monotonic()-observation.captured_at <= 2:
        raise NavigationError('小遊戲開始前的資源畫面已過期')
    metadata = {'task': task, 'cost': cost, 'resource': asdict(resource), 'budget_remaining': budget}
    if nav.recording:
        nav.recording.mark('minigame_start_verified', **metadata)
    before_input()
    x, y, w, h = buttons[0].box
    nav.inputs.click(int(x+w/2), int(y+h/2))
    return metadata


class MinigameResults:
    def __init__(self, navigator, task):
        self.nav = navigator
        self.scene, self.setup, self.prefix, self.label, self.summary_label = {
            'jump_rope': ('jump_result', 'jump_setup', 'jump', '跳繩', '本次跳躍次數'),
            'cooking': ('cooking_result', 'cooking_setup', 'cooking', '料理', '本次分數'),
        }[task]

    def result_page(self, *, timeout=15):
        deadline = time.monotonic()+timeout
        while True:
            observation = self.nav.observe()
            if observation.scene == self.scene:
                title = self.nav.text(observation, r'^SCORE\s*ATTACK$', (600, 130, 600, 200))
                buttons = self.nav.text(observation, '下一步$', (980, 610, 260, 90))
                if len(title) == len(buttons) == 1:
                    return observation, buttons[0].box
            if time.monotonic() >= deadline:
                self.nav.save(observation, self.prefix+'-result-not-confirmed')
                raise NavigationError(self.label+'模組結束後未確認分數結果頁')
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
                self.nav.save(updated, self.prefix+'-rewards-confirmed')
                return self.close_reward(button)
            if time.monotonic() >= deadline:
                self.nav.save(updated, self.prefix+'-rewards-not-confirmed')
                raise NavigationError(self.label+'獎勵頁尚未確認，未重複下一步')
            self.nav.inputs.pause(.2)

    def reward_button(self, observation):
        if observation.scene != self.scene:
            return None
        labels = self.nav.text(observation, '('+self.summary_label+'|獲得獎勵)$', (610, 220, 340, 210))
        if (self.prefix == 'cooking'
                and not any(item.text.strip().endswith(self.summary_label) for item in labels)):
            labels += self.nav.text(observation, '^本次分數$', (680, 230, 160, 50))
        if any(sum(item.text.strip().endswith(label) for item in labels) != 1
               for label in (self.summary_label, '獲得獎勵')):
            return None
        buttons = self.nav.text(observation, '^OK[!！]?$', (980, 610, 260, 90))
        if not buttons:
            buttons = self.nav.text(observation, '^OK[!！]?$', (1055, 630, 90, 35))
        return buttons[0].box if len(buttons) == 1 else None

    def close_reward(self, button):
        x, y, w, h = button
        self.nav.inputs.click(int(x+w/2), int(y+h/2))
        return self.finish_event()

    def event_button(self, observation):
        if observation.scene != self.scene:
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
            if observation.scene in {self.setup, 'home'}:
                return self.nav.wait_for({self.setup, 'home'}, timeout=5)
            button = button or self.event_button(observation)
            if button is not None and not clicked:
                self.nav.save(observation, self.prefix+'-event-result')
                x, y, w, h = button
                self.nav.inputs.click(int(x+w/2), int(y+h/2))
                clicked = True
            if time.monotonic() >= deadline:
                self.nav.save(observation, self.prefix+'-result-return-stalled')
                raise NavigationError(self.label+'結果尚未返回設定頁，未重複確認')
            self.nav.inputs.pause(.2)
            observation = button = None
