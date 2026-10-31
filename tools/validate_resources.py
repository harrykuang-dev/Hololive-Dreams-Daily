"""Validate interface/pipeline schemas, references, and required blank counts."""
import json
from pathlib import Path
import sys

import jsonschema
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from daily.contracts import USER_ENTERED_ROUNDS


def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def validate():
    interface = load('interface.json')
    pipeline = load('resource/pipeline/tasks.json')
    jsonschema.validate(interface, load('schemas/interface.schema.json'))
    base = 'https://local.invalid/schemas/'
    registry = Registry().with_resources(
        (base + path.name, Resource.from_contents(load('schemas/' + path.name)))
        for path in (ROOT / 'schemas').glob('*.json'))
    pipeline_schema = load('schemas/pipeline.schema.json')
    pipeline_schema['$id'] = base + 'pipeline.schema.json'
    jsonschema.Draft202012Validator(pipeline_schema, registry=registry).validate(pipeline)
    for lang_path in interface['languages'].values():
        strings = load(lang_path)
        def walk(value):
            if isinstance(value, str) and value.startswith('$'):
                assert value[1:] in strings, value
            elif isinstance(value, dict):
                for child in value.values(): walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(interface)
    for task in interface['task']:
        assert task['entry'] in pipeline
        assert all(option in interface['option'] for option in task['option'])
    for task in USER_ENTERED_ROUNDS:
        field = interface['option'][task + '_rounds']['inputs'][0]
        assert 'default' not in field
        assert pipeline['Daily_' + task]['custom_action_param']['rounds'] == ''
    print(f'Validated {len(interface["task"])} tasks, {len(interface["option"])} options and two languages.')


if __name__ == '__main__':
    validate()
