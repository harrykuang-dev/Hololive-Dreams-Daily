from types import SimpleNamespace

import pytest

from daily.minigames import MinigameResults, start_minigame
from daily.navigation import NavigationError


@pytest.mark.parametrize('wide_count,allowed,narrow_used', [(0,True,True),(1,True,False),(2,False,False)])
def test_cooking_reward_fallback_requires_unique_summary_and_exact_ok(wide_count, allowed, narrow_used):
    calls=[]
    def text(observation, expected, roi):
        calls.append(roi)
        if '本次分數' in expected:
            return [SimpleNamespace(text=value) for value in ('本次分數','獲得獎勵')]
        count = wide_count if roi == (980,610,260,90) else 1
        return [SimpleNamespace(box=(1060,640,55,25))]*count
    results=MinigameResults(SimpleNamespace(text=text),'cooking')
    assert (results.reward_button(SimpleNamespace(scene='cooking_result')) is not None) == allowed
    assert ((1055,630,90,35) in calls) == narrow_used


def test_live_cannot_be_a_minigame_result_handler():
    with pytest.raises(KeyError):
        MinigameResults(object(),'solo_live')


@pytest.mark.parametrize('summary_count,narrow_count,allowed', [(0,1,True),(0,0,False),
                                                            (0,2,False),(2,1,False)])
def test_cooking_summary_fallback_keeps_both_headings_unique(summary_count,narrow_count,allowed):
    calls=[]
    def text(observation, expected, roi):
        calls.append(roi)
        if roi == (610,220,340,210):
            return [SimpleNamespace(text='獲得獎勵')]+[SimpleNamespace(text='本次分數')]*summary_count
        if roi == (680,230,160,50):
            return [SimpleNamespace(text='本次分數')]*narrow_count
        return [SimpleNamespace(box=(1060,640,55,25))]
    results=MinigameResults(SimpleNamespace(text=text),'cooking')
    assert (results.reward_button(SimpleNamespace(scene='cooking_result')) is not None)==allowed
    assert ((680,230,160,50) in calls)==(summary_count==0)
