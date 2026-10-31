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
from .settings import read_settings
from .resources import EnergySettings, ResourceUnavailable
from .live import SoloLiveRunner
from .holowork import HoloWorkRunner
from .recording import GameRecording, recording_directory

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
        navigator = recording = journal = config = result = None

        def report(value):
            nonlocal result
            result = value
            if recording:
                recording.mark('task_result', **value.to_dict())
            journal.record(config, value, session=self.session,
                           recording=recording.paths() if recording else None)

        def stopped(status, reason):
            return TaskResult(status, reason,
                              result.completed_rounds if result else 0,
                              result.evidence if result else None,
                              pending_round=result.pending_round if result else False)

        try:
            raw = read_settings(context, argv.node_name.removeprefix('Daily_'))
            config = TaskConfig.parse(raw)
            data = Path(os.environ.get('PI_USER_DATA_DIR', ROOT / 'userdata')) / 'daily'
            journal = Journal(data / 'results')
            recording = GameRecording(context.tasker.controller, recording_directory(ROOT, data),
                                      f'{self.session}-{config.task}')
            recording.start()
            navigator = Navigator(context, ROOT / 'resource', data / 'evidence' / self.session,
                                  recording=recording)
            report(TaskResult(TaskStatus.RUNNING, '執行中'))
            report(self.execute(navigator, config, progress=report))
        except ResourceUnavailable as error:
            result = stopped(TaskStatus.SKIPPED, str(error))
        except Cancelled:
            result = stopped(TaskStatus.CANCELLED, '已停止')
        except Exception as error:
            print(f'執行錯誤：{type(error).__name__}: {error}', flush=True)
            result = stopped(TaskStatus.FAILED, str(error))
        finally:
            try:
                try:
                    if navigator is not None:
                        navigator.inputs.release_all()
                except Exception as error:
                    result = stopped(TaskStatus.FAILED, f'釋放輸入失敗：{error}')
                try:
                    if recording is not None:
                        try:
                            recording.mark('task_end', **result.to_dict())
                        finally:
                            recording.stop()
                except Exception as error:
                    result = stopped(TaskStatus.FAILED, f'{result.reason}；錄影未完整驗證：{error}')
                if journal is not None and config is not None:
                    journal.record(config, result, session=self.session,
                                   recording=recording.paths() if recording else None)
            finally:
                self._lock.release()
        print(json.dumps(result.to_dict(), ensure_ascii=False), flush=True)
        # An entry check is successful navigation, not completed gameplay.
        return result.status in (TaskStatus.COMPLETE, TaskStatus.ENTRY_VERIFIED,
                                 TaskStatus.NAVIGATION_VERIFIED, TaskStatus.SKIPPED)

    def execute(self, navigator: Navigator, config: TaskConfig, *, progress=lambda result: None) -> TaskResult:
        if config.task == 'connect':
            observation = navigator.observe()
            evidence = navigator.save(observation, 'connected')
            if observation.scene is None:
                raise NavigationError('已連接遊戲，但目前畫面尚未辨識；請先回到樂園')
            return TaskResult(TaskStatus.COMPLETE, f'已確認畫面：{observation.scene}', evidence=evidence)
        routes = {'fishing': navigator.to_fishing_second,
                  'holowork': navigator.to_holowork,
                  'solo_live': navigator.to_solo_setup,
                  'multi_live': navigator.to_live_portal,
                  'cooking': lambda: navigator.to_minigame_setup('cooking'),
                  'jump_rope': lambda: navigator.to_minigame_setup('jump_rope'),
                  'rewards': navigator.to_daily_missions}
        route = routes.get(config.task)
        if route is None:
            raise NavigationError('此任務導航仍在開發中，尚未啟用消耗操作')
        observation = route()
        evidence = navigator.save(observation, config.task + '-entry')
        if config.entry_only:
            actual_entry = config.task in ('fishing', 'rewards', 'cooking', 'jump_rope', 'solo_live', 'holowork')
            return TaskResult(TaskStatus.ENTRY_VERIFIED if actual_entry else TaskStatus.NAVIGATION_VERIFIED,
                              '互動入口已驗證，未執行消耗操作' if actual_entry else
                              '已驗證選單導航，尚未驗證遊戲開始入口', evidence=evidence)
        if config.task == 'solo_live':
            return SoloLiveRunner(navigator, progress).run(config)
        if config.task == 'holowork':
            return HoloWorkRunner(navigator, progress).run()
        if config.task in ('cooking', 'jump_rope'):
            EnergySettings(navigator).configure(config.task, config.multiplier, config.resource_budget)
        raise NavigationError('已到達導航入口；完整任務執行尚未啟用')
