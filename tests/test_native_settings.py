"""Exercise actual Maa override semantics with a controller that refuses input."""
from pathlib import Path
import sys

import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

from daily.contracts import ConfigurationError, TaskConfig
from daily.settings import defaults, read_settings, setting_node

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from offline_controller import OfflineController


class InspectSettings(CustomAction):
    def __init__(self):
        super().__init__()
        self.calls = []

    def run(self, context, argv):
        self.calls.append(read_settings(context, argv.node_name.removeprefix('Daily_')))
        return True


def test_multiple_ui_options_survive_real_maa_overrides(tmp_path):
    Toolkit.init_option(str(tmp_path / 'maa'))
    resource = Resource()
    inspect = InspectSettings()
    resource.register_custom_action('RunDailyTask', inspect)
    assert resource.post_bundle(ROOT / 'resource').wait().succeeded
    controller = OfflineController(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    cases = [('cooking', {'rounds': '7', 'multiplier': '3', 'resource_budget': '180', 'entry_only': True}),
             ('solo_live', {'rounds': '2', 'multiplier': '5', 'resource_budget': '10',
                            'team': '4', 'song': 'Miracle Palette', 'entry_only': False}),
             ('high_low', {'target': '40000', 'entry_only': False})]
    for task, options in cases:
        overrides = [{setting_node(task, field): {'custom_action_param': {field: value}}}
                     for field, value in options.items()]
        before = len(inspect.calls)
        assert tasker.post_task('Daily_'+task, overrides).wait().succeeded
        assert len(inspect.calls) == before+1  # Setting nodes are never executed.
        assert inspect.calls[-1] == {'task': task, **defaults(task), **options}
        TaskConfig.parse(inspect.calls[-1])
    assert tasker.post_task('Daily_multi_live').wait().succeeded
    with pytest.raises(ConfigurationError, match='局數'):
        TaskConfig.parse(inspect.calls[-1])
    assert not controller.input_attempts
