import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from daily import cooking
from daily.contracts import ResourceObservation, TaskConfig
from daily.cooking import CookingRunner
from daily.navigation import NavigationError
from daily.resources import EnergySelection
from daily.workers import WorkerOutcome


def runner(tmp_path, monkeypatch, *, verified=True):
    python=tmp_path/'python.exe';python.touch()
    module=tmp_path/'modules/cooking/outputs/kitchen-bot'
    module.mkdir(parents=True);(module/'standalone.py').touch()
    (tmp_path/'config').mkdir()
    (tmp_path/'config/modules.json').write_text(json.dumps({'python':str(python),'module_root':str(tmp_path/'modules')}))
    budgets,rounds,clicks=[],[],[]
    class Energy:
        def __init__(self,nav):pass
        def configure(self,task,multiplier,budget):
            budgets.append(budget)
            return EnergySelection(1,30,ResourceObservation(100,100))
    monkeypatch.setattr(cooking,'EnergySettings',Energy)
    class Worker:
        def __init__(self,*args,**kwargs):pass
        def run(self,config,*,on_ready,**kwargs):
            rounds.append(config.rounds)
            on_ready({'phase':'capture_ready'})
            return WorkerOutcome(1,{'result':{'verified':verified,'score':123,'completed_dishes':2}})
    def text(observation,expected,roi):
        if expected=='^遊玩$':return [SimpleNamespace(box=(1000,620,200,60))]
        if expected=='.*':return [SimpleNamespace(text='×30×3',box=(850,590,140,30))]
        return [SimpleNamespace(text='100/100')]
    nav=SimpleNamespace(observe=lambda:SimpleNamespace(scene='cooking_setup',captured_at=time.monotonic()),
        text=text,to_minigame_setup=lambda task:None,recording=None,
        save=lambda *args:'result.png',context=SimpleNamespace(tasker=SimpleNamespace(stopping=False)),
        controller=SimpleNamespace(post_touch_up=lambda contact:None),
        inputs=SimpleNamespace(check=lambda:None,wait=lambda job:None,click=lambda *args:clicks.append(args)))
    result=CookingRunner(nav,tmp_path,tmp_path/'data',supervisor=Worker)
    result.result_page=lambda:(object(),None)
    result.finish_result=lambda:None
    return result,budgets,rounds,clicks


def test_cooking_launches_one_authorized_worker_per_user_round_and_decrements_budget(tmp_path,monkeypatch):
    program,budgets,rounds,clicks=runner(tmp_path,monkeypatch)
    result=program.run(TaskConfig('cooking',rounds=2,multiplier=1,resource_budget=60))
    assert result.completed_rounds==2 and result.status=='complete'
    assert budgets==[60,30] and rounds==[1,1] and len(clicks)==2


def test_unverified_worker_result_never_counts_a_cooking_round(tmp_path,monkeypatch):
    program,_,_,_=runner(tmp_path,monkeypatch,verified=False)
    program.result_page=lambda:pytest.fail('Unverified module result must stop before result navigation')
    with pytest.raises(NavigationError,match='分數'):
        program.run(TaskConfig('cooking',rounds=1,resource_budget=30))
