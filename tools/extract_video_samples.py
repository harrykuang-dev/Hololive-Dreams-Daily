"""Extract game-only frames; reject desktop or ambiguous window geometry."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = [
    ('solo_setup', '13.51.56.03', 50),
    ('song_select', '13.51.56.03', 40),
    ('live_portal', '13.51.56.03', 20),
    ('auto_playing', '13.51.56.03', 100),
    ('home', '13.51.56.03', 1995),
    ('daily_missions', '13.51.56.03', 2000),
    ('achievements', '13.51.56.03', 2010),
    ('event_missions', '13.51.56.03', 2020),
    ('fishing_map_second', '13.51.56.03', 1910),
    ('fishing_map_popup', '13.51.56.03', 1910.6),
    ('fishing_setup_second', '13.51.56.03', 1918),
    ('fishing_setup_first', '13.51.56.03', 1906),
    ('multi_ready', '13.51.56.03', 1630),
    ('holowork', '09.52.54.01', 45),
    ('holowork_dispatch', '09.52.54.01', 50),
    ('notifications', '09.52.54.01', 43),
    ('minigame_portal', '09.52.54.01', 125),
    ('cooking_setup', '09.52.54.01', 127),
    ('cooking_energy', '09.52.54.01', 129),
    ('jump_setup', '13.51.56.03', 1370),
    ('gifts', '14.25.52.04', 6),
]


def locate_client(frame):
    # The Windows title band is wide, neutral and light. A taskbar or another
    # application's small title does not satisfy the width and aspect checks.
    low = frame.min(axis=2)
    high = frame.max(axis=2)
    mask = ((low >= 212) & (high - low <= 18)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 51), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w >= 1000 and 18 <= h <= 42 and y < frame.shape[0] // 2:
            height = round((w - 2) * 9 / 16)
            if y + h + height <= frame.shape[0]:
                candidates.append((x + 1, y + h, w - 2, height))
    if len(candidates) != 1:
        raise ValueError(f'Ambiguous game title geometry: {candidates}')
    return candidates[0]


def extract(base: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = []
    for label, video_id, seconds in SAMPLES:
        source = base / f'Desktop 2026.10.31 - {video_id}.mp4'
        cap = cv2.VideoCapture(str(source))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
            ok, frame = cap.read()
            if not ok: raise ValueError(f'Cannot read {source.name}:{seconds}')
        finally:
            cap.release()
        # Recording-specific client rectangles, visually checked against title,
        # all four edges and 16:9 content. The user moves the window mid-video.
        later_position = (video_id == '14.25.52.04' or
                          video_id == '13.51.56.03' and seconds >= 1350)
        x, y, w, h = (219, 115, 1484, 836) if later_position else (115, 133, 1484, 834)
        if x + w > frame.shape[1] or y + h > frame.shape[0]:
            raise ValueError('Recording no longer matches its calibrated viewport')
        image = cv2.resize(frame[y:y+h, x:x+w], (1280, 720), interpolation=cv2.INTER_AREA)
        path = destination / f'{label}.png'
        if not cv2.imwrite(str(path), image): raise OSError(path)
        manifest.append({'scene': label, 'video': source.name, 'seconds': seconds,
                         'client': [x, y, w, h], 'frame_size': [1280, 720],
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        print(label, [x, y, w, h], flush=True)
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--videos', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    extract(args.videos, args.output)
