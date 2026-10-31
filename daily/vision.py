"""Screen-local anchors and conservative Auto observations."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

import cv2
import numpy as np

from .contracts import AutoObservation


@dataclass(frozen=True)
class Match:
    name: str
    score: float
    box: tuple[int, int, int, int]


class Vision:
    def __init__(self, resources: Path):
        self.resources = resources
        self.anchors = json.loads((resources / 'anchors.json').read_text(encoding='utf-8'))
        self.images = {name: cv2.imread(str(resources / 'image' / record['file']))
                       for name, record in self.anchors.items()}
        if any(image is None for image in self.images.values()):
            raise FileNotFoundError('Missing UI anchor')

    @staticmethod
    def normalize(image):
        if image is None or image.size == 0:
            raise ValueError('Empty game frame')
        h, w = image.shape[:2]
        if abs(w / h - 16 / 9) > 0.04:
            raise ValueError('Game client must use 16:9 content')
        return cv2.resize(image, (1280, 720), interpolation=cv2.INTER_AREA)

    def match(self, frame, name: str) -> Match | None:
        entry = self.anchors[name]
        x, y, w, h = entry['roi']
        margin = 8
        x0, y0 = max(0, x-margin), max(0, y-margin)
        search = frame[y0:min(720, y+h+margin), x0:min(1280, x+w+margin)]
        template = self.images[name]
        scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(scores)
        if not np.isfinite(score) or score < entry['threshold']:
            return None
        return Match(name, score, (x0+location[0], y0+location[1], w, h))

    def scene(self, frame) -> str | None:
        candidates = [match for name in self.anchors if self.anchors[name].get('scene', True)
                      and '_auto_on' not in name
                      and (match := self.match(frame, name))]
        if not candidates:
            return None
        highest_priority = max(self.anchors[match.name].get('priority', 0) for match in candidates)
        candidates = [match for match in candidates
                      if self.anchors[match.name].get('priority', 0) == highest_priority]
        candidates.sort(key=lambda value: value.score, reverse=True)
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < .025:
            return None
        return candidates[0].name

    def find_all(self, frame, name: str, *, roi=None) -> list[Match]:
        x, y, w, h = roi or (0, 0, 1280, 720)
        template = self.images[name]
        th, tw = template.shape[:2]
        scores = cv2.matchTemplate(frame[y:y+h, x:x+w], template, cv2.TM_CCOEFF_NORMED)
        results = []
        while len(results) < 12:
            _, score, _, location = cv2.minMaxLoc(scores)
            if not np.isfinite(score) or score < self.anchors[name]['threshold']: break
            px, py = location
            results.append(Match(name, score, (x+px, y+py, tw, th)))
            scores[max(0,py-th):py+th, max(0,px-tw):px+tw] = -1
        return sorted(results, key=lambda result: (result.box[0], result.box[1]))

    def auto(self, frame, scene: str, remaining_text: str | None,
             captured_at: float | None = None) -> AutoObservation:
        anchor = {'solo_setup': 'solo_auto_on', 'multi_ready': 'multi_auto_on'}.get(scene)
        enabled = bool(anchor and self.match(frame, anchor))
        remaining = parse_remaining(remaining_text)
        return AutoObservation(enabled, remaining, scene,
                               time.monotonic() if captured_at is None else captured_at)


def parse_remaining(text: str | None) -> int | None:
    # Bare digits are accepted only when OCR was constrained to the Auto count ROI.
    if not text:
        return None
    match = re.fullmatch(r'\s*(?:還有\s*)?([0-9]+(?:\.[0-9]+)?)\s*([kKmM萬]?)\s*(?:次)?\s*', text)
    if not match:
        return None
    factor = {'': 1, 'k': 1000, 'm': 1000000, '萬': 10000}[match[2].lower()]
    value = float(match[1]) * factor
    return int(value) if value.is_integer() and value >= 0 else None
