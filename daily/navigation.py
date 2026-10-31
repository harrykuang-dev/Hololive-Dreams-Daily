"""Observed scene transitions. Consuming actions are separated from navigation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time

import cv2

from .inputs import Inputs
from .vision import Vision


class NavigationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Observation:
    image: object
    scene: str | None
    captured_at: float


class Navigator:
    def __init__(self, context, resources: Path, evidence: Path):
        self.context = context
        self.controller = context.tasker.controller
        self.inputs = Inputs(self.controller, stopping=lambda: context.tasker.stopping)
        self.vision = Vision(resources)
        self.evidence = evidence
        evidence.mkdir(parents=True, exist_ok=True)

    def observe(self) -> Observation:
        self.inputs.check()
        job = self.controller.post_screencap().wait()
        if not job.succeeded:
            raise NavigationError('無法擷取遊戲畫面')
        captured = time.monotonic()
        frame = self.vision.normalize(job.get())
        scene = self.vision.scene(frame)
        observation = Observation(frame, scene, captured)
        if scene is None:
            # The park backdrop and notification badges change. Require three
            # independently read toolbar labels instead of weakening a template.
            toolbar = self.text(observation, r'^(遊戲|Live|任務|成就)$', (700, 15, 480, 100))
            words = {getattr(item, 'text', '').strip() for item in toolbar}
            if len(words.intersection({'遊戲', 'Live', '任務', '成就'})) >= 3:
                scene = 'home'
        return Observation(frame, scene, captured)

    def save(self, observation: Observation, name: str) -> str:
        path = self.evidence / f'{name}-{time.time_ns()}.png'
        if not cv2.imwrite(str(path), observation.image):
            raise OSError('Cannot save scene evidence')
        return str(path)

    def wait_for(self, scenes, *, timeout=30) -> Observation:
        deadline = time.monotonic() + timeout
        while True:
            observation = self.observe()
            if observation.scene in scenes:
                return observation
            if time.monotonic() >= deadline:
                evidence = self.save(observation, 'unknown')
                raise NavigationError(f'等待畫面逾時：{", ".join(scenes)}；{evidence}')
            time.sleep(.2)

    def text(self, observation: Observation, expected: str, roi=None):
        # This is recognition only. The OCR pipeline has no input action.
        node = {'recognition': 'OCR', 'expected': [expected], 'threshold': .75}
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
        if observation.scene == 'home':
            return observation
        # These screens have the visibly recorded close button at top right.
        if observation.scene in {'live_portal', 'minigame_portal', 'daily_missions',
                                 'achievements', 'event_missions', 'gifts',
                                 'fishing_map_second', 'fishing_setup_second'}:
            self.inputs.click(1223, 55)
            return self.wait_for({'home'})
        raise NavigationError('目前不在可安全返回樂園的畫面')

    def to_live_portal(self):
        self.to_home()
        return self.click_text('home', '^Live$', 'live_portal', roi=(730, 10, 380, 100))

    def to_minigame_portal(self):
        self.to_home()
        return self.click_text('home', '^遊戲$', 'minigame_portal', roi=(730, 10, 380, 100))

    def to_daily_missions(self):
        self.to_home()
        return self.click_text('home', '^任務$', 'daily_missions', roi=(730, 10, 400, 110))

    def to_fishing_second(self):
        self.to_home()
        # The minimap opens the full map. Match its area title before selecting
        # the recorded second hook: upper hook is the first location, not ours.
        self.inputs.click(100, 100)
        self.wait_for({'fishing_map_second'})
        self.inputs.click(360, 462)
        home = self.wait_for({'home'}, timeout=45)
        matches = self.text(home, '開始釣魚', roi=(800, 360, 450, 220))
        if len(matches) != 1:
            raise NavigationError('第二釣點尚未出現開始釣魚的互動提示')
        self.inputs.key(0x45)  # E, from the recorded interaction prompt.
        return self.wait_for({'fishing_setup_second'})
