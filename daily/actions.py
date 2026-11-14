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
from .navigation import EntryUnavailable, NavigationError, Navigator
from .settings import read_settings
from .resources import ResourceUnavailable
from .live import SoloLiveRunner
from .holowork import HoloWorkRunner
from .recording import GameAudit, GameRecording, recording_directory, video_enabled
from .calendar import DailyAttempts, DailyUnavailable, game_day
from .park import GIFT_PLACES, ParkRunner
from .fishing import FishingRunner
from .high_low import HighLowRunner
from .jump import JumpRunner
from .cooking import CookingRunner
from .rewards import RewardsRunner

ROOT = Path(__file__).resolve().parents[1]
DAILY_STEPS = (
    ('holowork', 'holoWork', ('holowork',)),
    ('park_gifts', '角色禮物', tuple(place.key for place in GIFT_PLACES)),
    ('lottery', '刮刮樂', ('lottery',)),
    ('high_low', '比大小', ('high_low',)),
    ('cooking', '料理', ()),
    ('solo_live', '單人 Auto Live', ()),
    ('rewards', '任務獎勵與禮物箱', ()),
)


class RunDailyTask(CustomAction):
    def __init__(self):
        super().__init__()
        self.session = None
        self._lock = threading.Lock()

    def run(self, context, argv):
        if not self._lock.acquire(blocking=False):
            print('另一項日常仍持有遊戲控制權', flush=True)
            return False
        self.session = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
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
            label = f'{self.session}-{config.task}'
            if video_enabled(ROOT):
                recording = GameRecording(context.tasker.controller, recording_directory(ROOT, data), label)
            else:
                recording = GameAudit(context.tasker.controller, data / 'audit', label)
            recording.start()
            navigator = Navigator(context, ROOT / 'resource', data / 'evidence' / self.session,
                                  recording=recording)
            navigator.daily_attempts = DailyAttempts.from_runtime(ROOT, data, recording)
            report(TaskResult(TaskStatus.RUNNING, '執行中'))
            report(self.execute(navigator, config, progress=report))
        except (ResourceUnavailable, DailyUnavailable, EntryUnavailable) as error:
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
                    result = stopped(TaskStatus.FAILED, f'{result.reason}；執行紀錄未完整保存：{error}')
                if journal is not None and config is not None:
                    journal.record(config, result, session=self.session,
                                   recording=recording.paths() if recording else None)
            finally:
                self._lock.release()
        print(json.dumps(result.to_dict(), ensure_ascii=False), flush=True)
        if result.status == TaskStatus.SKIPPED and config is not None and config.task in {'cooking', 'jump_rope'}:
            # A requested game that never started must not look like a passed
            # gameplay task in the GUI. The journal retains the precise skip.
            return False
        # An entry check is successful navigation, not completed gameplay.
        return result.status in (TaskStatus.COMPLETE, TaskStatus.ENTRY_VERIFIED,
                                 TaskStatus.NAVIGATION_VERIFIED, TaskStatus.SKIPPED)

    def execute(self, navigator: Navigator, config: TaskConfig, *, progress=lambda result: None) -> TaskResult:
        if config.task == 'daily_routine':
            return self.run_routine(navigator, config, progress)
        if config.task == 'high_low' and not config.entry_only:
            data = Path(os.environ.get('PI_USER_DATA_DIR', ROOT / 'userdata')) / 'daily'
            return HighLowRunner(navigator, ROOT, data, progress).run(config)
        if config.task in ('park_gifts', 'lottery'):
            return ParkRunner(navigator, progress).run(config)
        if config.task == 'connect':
            observation = navigator.observe()
            evidence = navigator.save(observation, 'connected')
            if observation.scene is None:
                raise NavigationError('已連接遊戲，但目前畫面尚未辨識；請先回到樂園')
            return TaskResult(TaskStatus.COMPLETE, f'已確認畫面：{observation.scene}', evidence=evidence)
        routes = {'fishing': navigator.to_fishing_second,
                  'high_low': navigator.to_high_low_entry,
                  'holowork': navigator.to_holowork,
                  'solo_live': navigator.to_solo_setup,
                  'multi_live': navigator.to_multi_portal,
                  'cooking': lambda: navigator.to_minigame_setup('cooking'),
                  'jump_rope': lambda: navigator.to_minigame_setup('jump_rope'),
                  'rewards': navigator.to_daily_missions}
        route = routes.get(config.task)
        if route is None:
            raise NavigationError('此任務導航仍在開發中，尚未啟用消耗操作')
        observation = route()
        evidence = navigator.save(observation, config.task + '-entry')
        if config.entry_only:
            actual_entry = config.task in ('fishing', 'rewards', 'cooking', 'jump_rope', 'solo_live', 'holowork', 'high_low')
            return TaskResult(TaskStatus.ENTRY_VERIFIED if actual_entry else TaskStatus.NAVIGATION_VERIFIED,
                              '互動入口已驗證，未執行消耗操作' if actual_entry else
                              '已驗證選單導航，尚未驗證遊戲開始入口', evidence=evidence)
        if config.task == 'solo_live':
            return SoloLiveRunner(navigator, progress).run(config)
        if config.task == 'holowork':
            return self.run_holowork(navigator, progress)
        if config.task == 'rewards':
            return RewardsRunner(navigator, progress).run()
        if config.task == 'fishing':
            data = Path(os.environ.get('PI_USER_DATA_DIR', ROOT / 'userdata')) / 'daily'
            return FishingRunner(navigator, ROOT, data, progress).run(config)
        if config.task == 'jump_rope':
            data = Path(os.environ.get('PI_USER_DATA_DIR', ROOT / 'userdata')) / 'daily'
            return JumpRunner(navigator, ROOT, data, progress).run(config)
        if config.task == 'cooking':
            data = Path(os.environ.get('PI_USER_DATA_DIR', ROOT / 'userdata')) / 'daily'
            return CookingRunner(navigator, ROOT, data, progress).run(config)
        raise NavigationError('已到達導航入口；完整任務執行尚未啟用')

    def run_holowork(self, navigator, progress):
        ledger = navigator.daily_attempts
        attempt = None

        def reserve():
            nonlocal attempt
            if attempt is None:
                attempt = ledger.reserve('holowork')

        try:
            result = HoloWorkRunner(navigator, progress).run(before_change=reserve)
        except Exception as error:
            if attempt is not None:
                attempt.finish(error=error)
            raise
        if attempt is not None:
            attempt.finish(evidence=result.evidence)
        return result

    def run_routine(self, navigator, config, progress):
        ledger = navigator.daily_attempts
        day = game_day(ledger.clock())
        guard = ledger.guard

        def same_day():
            guard()
            if game_day(ledger.clock()) != day:
                raise Cancelled('遊戲已換日，已停止本輪日常')

        # Keep all daily reservations in the game day this run started in.
        ledger.guard = same_day
        try:
            return self.run_routine_steps(navigator, config, progress, same_day)
        finally:
            ledger.guard = guard

    def run_routine_steps(self, navigator, config, progress, same_day):
        ledger = navigator.daily_attempts
        data = Path(os.environ.get('PI_USER_DATA_DIR', ROOT / 'userdata')) / 'daily'
        outcomes, completed, evidence = [], 0, None
        pending = False
        for task, label, keys in DAILY_STEPS:
            same_day()
            navigator.inputs.check()
            verified = set(ledger.completed_tasks()) if keys else set()
            if keys and verified.issuperset(keys):
                completed += 1
                outcomes.append(f'{label}：今日已完成')
                continue
            options = TaskConfig(task, strategy=config.strategy)

            def update(value):
                nonlocal pending, evidence
                pending = value.pending_round
                evidence = value.evidence or evidence
                progress(TaskResult(TaskStatus.RUNNING, f'{label}：{value.reason}', completed,
                                    evidence, pending_round=pending))

            try:
                if task == 'cooking':
                    result = CookingRunner(navigator, ROOT, data, update).run(
                        TaskConfig(task, rounds=3, resource_budget=90), required_cost=30, daily_ledger=ledger)
                elif task == 'solo_live':
                    result = SoloLiveRunner(navigator, update).run(
                        TaskConfig(task, rounds=10, resource_budget=10), drain=True)
                elif task == 'rewards':
                    navigator.to_daily_missions()
                    result = RewardsRunner(navigator, update).run(missions_and_gifts_only=True)
                else:
                    if task == 'high_low' and task not in ledger.attempted_tasks():
                        current = navigator.observe()
                        # Preserve recovery from a table or open dialogue.
                        if current.scene == 'home':
                            navigator.open_notifications()
                    result = self.execute(navigator, options, progress=update)
            except (ResourceUnavailable, DailyUnavailable, EntryUnavailable) as error:
                result = TaskResult(TaskStatus.SKIPPED, str(error), pending_round=pending)
            except NavigationError as error:
                result = TaskResult(TaskStatus.FAILED, str(error), pending_round=pending)
            if result.status == TaskStatus.CANCELLED:
                raise Cancelled(result.reason)
            evidence = result.evidence or evidence
            pending = result.pending_round
            verified = set(ledger.completed_tasks()) if keys else set()
            # holoWork already running can be verified without claiming or dispatching.
            done = (result.status == TaskStatus.COMPLETE and not pending
                    and (task == 'holowork' or not keys or verified.issuperset(keys)))
            if done:
                completed += 1
            reason = result.reason
            if not done and result.status == TaskStatus.COMPLETE:
                reason += '，仍有每日項目未確認'
            outcomes.append(f'{label}：{reason}')
            if navigator.recording:
                navigator.recording.mark('routine_step', task=task, verified=done, **result.to_dict())
            progress(TaskResult(TaskStatus.RUNNING, '｜'.join(outcomes), completed, evidence,
                                pending_round=pending))
            if pending:
                break
        if not pending:
            same_day()
            try:
                navigator.to_home()
            except (EntryUnavailable, NavigationError) as error:
                outcomes.append(f'返回樂園：{error}')
                return TaskResult(TaskStatus.FAILED, '｜'.join(outcomes), completed, evidence)
        status = TaskStatus.COMPLETE if completed == len(DAILY_STEPS) else TaskStatus.FAILED
        return TaskResult(status, '｜'.join(outcomes), completed, evidence, pending_round=pending)
