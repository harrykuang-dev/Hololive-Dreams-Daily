"""Maa custom actions with explicit result and entry-only evidence."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import threading
import uuid

from maa.custom_action import CustomAction

from .contracts import ConfigurationError, TaskConfig, TaskResult, TaskStatus
from .inputs import Cancelled
from .journal import Journal
from .navigation import NavigationError, Navigator

ROOT = Path(__file__).resolve().parents[1]


class RunDailyTask(CustomAction):
    def __init__(self):
        super().__init__()
        self.session = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        self._lock = threading.Lock()

    def run(self, context, argv):
        if not self._lock.acquire(blocking=False):
            print('另一項日常仍持有遊戲控制權', flush=True)
            return False
        navigator = None
        try:
            raw = json.loads(argv.custom_action_param)
            raw.setdefault('task', argv.node_name.removeprefix('Daily_'))
            config = TaskConfig.parse(raw)
            data = Path(os.environ.get('PI_USER_DATA_DIR', ROOT / 'userdata')) / 'daily'
            journal = Journal(data / 'results')
            navigator = Navigator(context, ROOT / 'resource', data / 'evidence' / self.session)
            journal.record(config, TaskResult(TaskStatus.RUNNING, '執行中'), session=self.session)
            result = self.execute(navigator, config)
            journal.record(config, result, session=self.session)
            print(json.dumps(result.to_dict(), ensure_ascii=False), flush=True)
            # Skipped tasks remain explicit in the journal; an entry check is
            # successful navigation, not a completed daily gameplay task.
            return result.status in (TaskStatus.COMPLETE, TaskStatus.ENTRY_VERIFIED,
                                     TaskStatus.NAVIGATION_VERIFIED, TaskStatus.SKIPPED)
        except (ConfigurationError, ValueError, NavigationError) as error:
            print(f'任務停止：{error}', flush=True)
            if 'journal' in locals() and 'config' in locals():
                journal.record(config, TaskResult(TaskStatus.FAILED, str(error)), session=self.session)
            return False
        except Cancelled:
            if 'journal' in locals() and 'config' in locals():
                journal.record(config, TaskResult(TaskStatus.CANCELLED, '已停止'), session=self.session)
            return False
        except Exception as error:
            print(f'執行錯誤：{type(error).__name__}: {error}', flush=True)
            if 'journal' in locals() and 'config' in locals():
                journal.record(config, TaskResult(TaskStatus.FAILED, str(error)), session=self.session)
            return False
        finally:
            try:
                if navigator is not None:
                    navigator.inputs.release_all()
            finally:
                self._lock.release()

    def execute(self, navigator: Navigator, config: TaskConfig) -> TaskResult:
        if config.task == 'connect':
            observation = navigator.observe()
            evidence = navigator.save(observation, 'connected')
            if observation.scene is None:
                raise NavigationError('已連接遊戲，但目前畫面尚未辨識；請先回到樂園')
            return TaskResult(TaskStatus.COMPLETE, f'已確認畫面：{observation.scene}', evidence=evidence)
        routes = {'fishing': navigator.to_fishing_second,
                  'solo_live': navigator.to_live_portal,
                  'multi_live': navigator.to_live_portal,
                  'cooking': navigator.to_minigame_portal,
                  'jump_rope': navigator.to_minigame_portal,
                  'rewards': navigator.to_daily_missions}
        route = routes.get(config.task)
        if route is None:
            raise NavigationError('此任務導航仍在開發中，尚未啟用消耗操作')
        observation = route()
        evidence = navigator.save(observation, config.task + '-entry')
        if config.entry_only:
            actual_entry = config.task in ('fishing', 'rewards')
            return TaskResult(TaskStatus.ENTRY_VERIFIED if actual_entry else TaskStatus.NAVIGATION_VERIFIED,
                              '互動入口已驗證，未執行消耗操作' if actual_entry else
                              '已驗證選單導航，尚未驗證遊戲開始入口', evidence=evidence)
        raise NavigationError('已到達導航入口；完整任務執行尚未啟用')
