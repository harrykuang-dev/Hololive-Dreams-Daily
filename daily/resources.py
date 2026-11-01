"""Observe and configure the game's energy widget without replenishment."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import time
import cv2

from .contracts import ResourceObservation
from .navigation import NavigationError
from .vision import parse_remaining


class ResourceUnavailable(NavigationError):
    pass


def parse_fraction(text):
    if not text:
        return ResourceObservation(None, None)
    # The adjacent replenishment icon can be joined to the same OCR box.
    # Accept exactly that trailing glyph; never repair or infer missing digits.
    compact = re.sub(r'\s+', '', text).replace('／', '/')
    parts = re.sub(r'[+＋]$', '', compact).split('/')
    if len(parts) != 2:
        return ResourceObservation(None, None)
    return ResourceObservation(*(parse_remaining(value) for value in parts))


def parse_multiplier(text):
    match = re.fullmatch(r'\s*[×xX]\s*([0-9]+)\s*', text or '')
    return int(match[1]) if match else None


def parse_consumption(text):
    match = re.fullmatch(r'\s*[×xX]\s*([0-9]+)(?:\s*[|丨｜]?\s*[×xX]\s*[0-9]+)?\s*', text or '')
    return int(match[1]) if match else None


def read_setup_cost(nav, observation, *, multiplayer=False):
    if not multiplayer and getattr(observation, 'scene', None) == 'solo_setup':
        # This box ends before the separate gift/reward multiplier. The text
        # detector can omit the leading multiplication sign at higher costs.
        items = nav.text(observation, r'^[×xX]?\s*[0-9]{1,2}$', (840, 586, 72, 37))
        values = [match[1] for item in items if
                  (match := re.fullmatch(r'[×xX]?\s*([0-9]{1,2})', item.text.strip()))]
        return int(values[0]) if len(values) == 1 else None
    roi = (480, 580, 180, 45) if multiplayer else (830, 580, 180, 45)
    parts = nav.text(observation, '.*', roi)
    return parse_consumption(''.join(item.text for item in sorted(parts, key=lambda item: item.box[0])))


def read_one(nav, observation, expected, roi):
    items = nav.text(observation, expected, roi)
    return items[0].text if len(items) == 1 else None


@dataclass(frozen=True)
class EnergySelection:
    multiplier: int
    cost: int
    resource: ResourceObservation
    empty_energy_play: bool = False

    @property
    def effective_cost(self):
        return 0 if self.empty_energy_play and self.resource.available == 0 else self.cost


class EnergySettings:
    def __init__(self, navigator):
        self.nav = navigator

    def read(self, observation):
        if observation.scene != 'energy_settings':
            raise NavigationError('目前不是能量設定畫面')
        # The cyan counter has little luminance contrast. Its red channel gives
        # dark digits on a light field; recognition-only uses this fixed widget
        # ROI so the text detector cannot silently omit the large styled digit.
        enhanced = replace(observation, image=cv2.cvtColor(observation.image[:, :, 2], cv2.COLOR_GRAY2BGR))
        numbers = self.nav.text(enhanced, '^[0-9]+$', (1145, 339, 62, 65), only_rec=True)
        multiplier = parse_remaining(numbers[0].text) if len(numbers) == 1 else None
        costs = self.nav.text(observation, '^[0-9]+$', (975, 545, 100, 70), only_rec=True)
        cost = parse_remaining(costs[0].text) if len(costs) == 1 else None
        fraction = read_one(self.nav, observation, '[0-9].*[/／].*[0-9]', (1025, 25, 145, 60))
        resource = parse_fraction(fraction)
        if multiplier is None or cost is None:
            raise NavigationError('無法確認能量倍率與實際消耗量')
        empty_energy_play = False
        if resource.available == 0:
            notes = self.nav.text(observation, '.+', (820, 485, 300, 50))
            normalized = [re.sub(r'[\s，,]', '', item.text).replace('时', '時').replace('将', '將') for item in notes]
            empty_energy_play = normalized.count('能量不足時將消耗目前的能量') == 1
        return EnergySelection(multiplier, cost, resource, empty_energy_play)

    def configure(self, task, multiplier, budget_remaining=None):
        observation = self.nav.open_energy_settings(task)
        observation, selection = self.wait_read(observation)
        for _ in range(100):
            if selection.multiplier == multiplier:
                break
            anchor = 'energy_minus' if selection.multiplier > multiplier else 'energy_plus'
            button = self.nav.vision.match(observation.image, anchor)
            if button is None:
                raise NavigationError('所選倍率超出目前能量設定範圍，或調整按鈕無法辨識')
            x, y, w, h = button.box
            self.nav.inputs.click(x+w//2, y+h//2)
            observation, updated = self.wait_read(self.nav.observe(), changed_from=selection.multiplier)
            # One button press must change the setting in the requested direction.
            direction = 1 if anchor == 'energy_plus' else -1
            if (updated.multiplier-selection.multiplier)*direction <= 0:
                raise NavigationError('能量倍率變更方向異常')
            if abs(updated.multiplier-multiplier) >= abs(selection.multiplier-multiplier):
                raise NavigationError('無法精確設定所選倍率')
            selection = updated
        else:
            raise NavigationError('能量倍率調整次數已達上限')
        if selection.cost < 0 or not selection.resource.permits(selection.effective_cost, budget_remaining):
            self.nav.cancel_energy_settings()
            raise ResourceUnavailable('現有能量或本次消耗上限不足，未開始遊玩')
        if selection.empty_energy_play and getattr(self.nav, 'recording', None) is not None:
            evidence = self.nav.save(observation, 'energy-empty-policy')
            self.nav.recording.mark('energy_empty_policy_verified', nominal_cost=selection.cost,
                                    resource=selection.resource.__dict__, evidence=evidence)
        target_scene = {'solo_live': 'solo_setup', 'cooking': 'cooking_setup', 'jump_rope': 'jump_setup'}[task]
        setup = self.nav.click_text('energy_settings', '決定$', target_scene, roi=(970, 625, 240, 75))
        if read_setup_cost(self.nav, setup) != selection.effective_cost:
            evidence = self.nav.save(setup, 'energy-confirmed-cost-mismatch')
            raise NavigationError(f'設定後的實際消耗量不符，未開始遊玩；{evidence}')
        return selection

    def wait_read(self, observation, *, changed_from=None, timeout=5):
        deadline, previous = time.monotonic()+timeout, None
        while True:
            if observation.scene != 'energy_settings':
                raise NavigationError('調整能量時畫面已改變')
            try:
                selection = self.read(observation)
            except NavigationError:
                selection = None
            # Digits animate after +/-; require two complete, identical reads.
            if selection is not None and selection.multiplier != changed_from and selection == previous:
                return observation, selection
            previous = selection
            if time.monotonic() >= deadline:
                self.nav.save(observation, 'energy-value-not-stable')
                raise NavigationError('能量數值未能穩定確認，停止調整')
            self.nav.inputs.pause(.2)
            observation = self.nav.observe()
