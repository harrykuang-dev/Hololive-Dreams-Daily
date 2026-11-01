"""Generate Project Interface V2 and Maa pipeline entries from one task catalog."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from daily.contracts import TASKS, USER_ENTERED_ROUNDS
from daily.settings import defaults, setting_node

LABELS = {
    'connect': ('連接遊戲', 'Connect game'),
    'holowork': ('holoWork 收取與派遣', 'Collect and dispatch holoWork'),
    'park_gifts': ('樂園角色禮物', 'Park character gifts'),
    'lottery': ('樂園抽獎', 'Park lottery'),
    'cooking': ('料理', 'Cooking'),
    'solo_live': ('單人 Live（僅 Auto）', 'Solo Live (Auto only)'),
    'high_low': ('High & Low', 'High & Low'),
    'jump_rope': ('跳繩', 'Jump Rope'),
    'multi_live': ('多人 Live（僅 Auto）', 'Multiplayer Live (Auto only)'),
    'fishing': ('釣魚（第二釣點）', 'Fishing (second location)'),
    'rewards': ('集中領取獎勵', 'Claim rewards'),
}


def write(path, value):
    destination = ROOT / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def generate():
    interface = {
        'interface_version': 2, 'name': 'HololiveDreamsDaily',
        'label': '$title', 'title': '$title', 'version': '0.1.0',
        'languages': {'zh_tw': 'interface_zh_tw.json', 'en_us': 'interface_en_us.json'},
        'description': '$description', 'license': 'THIRD_PARTY_NOTICES.md',
        'controller': [{'name': 'SteamWin32', 'label': '$controller', 'type': 'Win32',
                        'display_short_side': 720,
                        'win32': {'class_regex': '^UnityWndClass$',
                                  'window_regex': '^hololive-Dreams$', 'screencap': 'FramePool',
                                  'mouse': 'Seize', 'keyboard': 'Seize'}}],
        'resource': [{'name': 'TraditionalChinese', 'label': '$resource', 'path': ['./resource']}],
        'agent': {'child_exec': './runtime/python/python.exe', 'child_args': ['./agent/main.py']},
        'task': [], 'option': {},
    }
    translations = {
        'zh_tw': {'title': 'hololive-Dreams 日常',
                  'description': '以遊戲內建 Auto 完成 Live，並整合四個小遊戲與日常流程。',
                  'controller': 'Steam 客戶端（前景）', 'resource': '繁體中文'},
        'en_us': {'title': 'hololive-Dreams Daily',
                  'description': 'Daily routines and four minigames. Live uses the game-provided Auto only.',
                  'controller': 'Steam client (foreground)', 'resource': 'Traditional Chinese'},
    }
    pipeline = {'DailyOCR': {'recognition': 'OCR', 'expected': ['.*'],
                              'action': 'DoNothing', 'threshold': 0.75}}
    for task in TASKS:
        entry = 'Daily_' + task
        config = {'task': task, 'rounds': '' if task in USER_ENTERED_ROUNDS else '1',
                  'entry_only': False, 'multiplier': '1', 'resource_budget': ''}
        pipeline[entry] = {'recognition': 'DirectHit', 'action': 'Custom',
                           'custom_action': 'RunDailyTask', 'custom_action_param': config,
                           'focus': {'Node.Action.Starting': '$starting',
                                     'Node.Action.Succeeded': '$finished', 'Node.Action.Failed': '$failed'}}
        # custom_action_param is replaced as a whole by each Maa override.
        # Keep one independent node per option so later options cannot discard
        # previously entered counts, resource limits or the entry-only flag.
        for field, value in defaults(task).items():
            pipeline[setting_node(task, field)] = {
                'recognition': 'DirectHit', 'action': 'Custom',
                'custom_action': 'RunDailyTask', 'custom_action_param': {field: value}}
        row = {'name': task, 'label': '$task_' + task, 'entry': entry,
               'default_check': task == 'connect', 'option': []}
        for lang, index in [('zh_tw', 0), ('en_us', 1)]:
            translations[lang]['task_' + task] = LABELS[task][index]

        def add_input(field, zh, en, *, default=None, verify=r'^[1-9][0-9]{0,2}$'):
            option_name = task + '_' + field
            input_item = {'name': field, 'label': '$' + option_name,
                          'pipeline_type': 'string', 'verify': verify,
                          'pattern_msg': '$invalid_' + field}
            if default is not None:
                input_item['default'] = default
            interface['option'][option_name] = {
                'type': 'input', 'label': '$' + option_name, 'inputs': [input_item],
                'pipeline_override': {setting_node(task, field): {'custom_action_param': {field: '{' + field + '}'}}}}
            row['option'].append(option_name)
            translations['zh_tw'][option_name] = zh
            translations['en_us'][option_name] = en
            translations['zh_tw']['invalid_' + field] = '請填入有效數值'
            translations['en_us']['invalid_' + field] = 'Enter a valid value'

        if task in USER_ENTERED_ROUNDS or task == 'jump_rope':
            add_input('rounds', '局數（必填）', 'Rounds (required)',
                      default=None if task in USER_ENTERED_ROUNDS else '1')
        if task in ('cooking', 'solo_live', 'multi_live', 'jump_rope'):
            add_input('multiplier', '消耗倍率', 'Consumption multiplier', default='1', verify=r'^[1-9][0-9]?$')
            add_input('resource_budget', '本次消耗上限（可空白）',
                      'Resource budget (optional)', default='', verify=r'^$|^(0|[1-9][0-9]{0,8})$')
            interface['option'][task + '_resource_budget']['description'] = (
                '空白時以現有資源餘量為限；0 表示不消耗資源，不會自動補充。')
        if task in ('fishing', 'high_low'):
            add_input('target', '目標釣獲數' if task == 'fishing' else '本次收益目標（空白為遊戲上限）',
                      'Target catches' if task == 'fishing' else 'Profit target (blank: game cap)',
                      default='3' if task == 'fishing' else '', verify=r'^$|^[1-9][0-9]{0,8}$')
        if task == 'jump_rope':
            add_input('score_limit', '跳繩停止分數（可空白）', 'Jump Rope stop score (optional)',
                      default='', verify=r'^$|^[1-9][0-9]{0,2}$')
        if task in ('solo_live', 'multi_live'):
            add_input('team', '隊伍編號（current 為沿用）', 'Team slot (current: keep)',
                      default='current', verify=r'^current$|^[1-9][0-9]?$')
        if task == 'solo_live':
            add_input('song', '歌曲（空白為沿用）', 'Song (blank: keep)', default='', verify=r'^.*$')
        option_name = task + '_entry_only'
        interface['option'][option_name] = {
            'type': 'switch', 'label': '$entry_only', 'default_case': 'No',
            'cases': [{'name': 'No', 'label': '$no',
                       'pipeline_override': {setting_node(task, 'entry_only'): {'custom_action_param': {'entry_only': False}}}},
                      {'name': 'Yes', 'label': '$yes',
                       'pipeline_override': {setting_node(task, 'entry_only'): {'custom_action_param': {'entry_only': True}}}}]}
        row['option'].append(option_name)
        interface['task'].append(row)
    for lang in translations:
        translations[lang].update(
            {'entry_only': '僅驗證互動入口' if lang == 'zh_tw' else 'Verify interaction entry only',
             'yes': '是' if lang == 'zh_tw' else 'Yes', 'no': '否' if lang == 'zh_tw' else 'No',
             'starting': '開始執行 {name}' if lang == 'zh_tw' else 'Starting {name}',
             'finished': '執行結束 {name}；詳細結果見任務紀錄' if lang == 'zh_tw' else 'Finished {name}; see task record for the result',
             'failed': '任務停止 {name}' if lang == 'zh_tw' else 'Task stopped: {name}'})
        write(f'interface_{lang}.json', translations[lang])
    write('interface.json', interface)
    write('resource/pipeline/tasks.json', pipeline)


if __name__ == '__main__':
    generate()
