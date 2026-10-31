"""Read independently overridden settings from the current Maa task context."""
from __future__ import annotations

from .contracts import ConfigurationError, TASKS, USER_ENTERED_ROUNDS


def defaults(task):
    values = {'entry_only': False}
    if task in USER_ENTERED_ROUNDS or task == 'jump_rope':
        values['rounds'] = '' if task in USER_ENTERED_ROUNDS else '1'
    if task in ('cooking', 'solo_live', 'multi_live', 'jump_rope'):
        values.update(multiplier='1', resource_budget='')
    if task in ('fishing', 'high_low'):
        values['target'] = '3' if task == 'fishing' else ''
    if task in ('solo_live', 'multi_live'):
        values['team'] = 'current'
    if task == 'solo_live':
        values['song'] = ''
    return values


def setting_node(task, field):
    return f'DailyConfig_{task}_{field}'


def read_settings(context, task):
    if task not in TASKS:
        raise ConfigurationError('未知任務')
    values = {'task': task}
    for field in defaults(task):
        node = context.get_node_data(setting_node(task, field))
        # MaaContextGetNodeData returns the normalized V2 action structure.
        action = node.get('action', {}) if isinstance(node, dict) else {}
        parameters = action.get('param', {}).get('custom_action_param', {})
        if action.get('type') != 'Custom' or not isinstance(parameters, dict) or set(parameters) != {field}:
            raise ConfigurationError(f'無法讀取任務設定：{field}')
        values[field] = parameters[field]
    return values
