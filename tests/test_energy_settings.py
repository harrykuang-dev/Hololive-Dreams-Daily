from types import SimpleNamespace

import pytest

from daily.contracts import ResourceObservation
from daily.resources import EnergySelection, EnergySettings, ResourceUnavailable, parse_fraction, record_post_round_energy
from daily.navigation import NavigationError, Observation
import numpy as np


@pytest.mark.parametrize('text,available,capacity', [
    ('8/10+', 8, 10), ('100/200', 100, 200), (' 3 ／ 20 ', 3, 20),
    ('8/1O', 8, None), ('8/10++', 8, None), ('8/10/20', None, None),
    ('', None, None)])
def test_resource_fraction_keeps_numbers_and_rejects_ambiguous_ocr(text, available, capacity):
    assert parse_fraction(text) == ResourceObservation(available, capacity)


@pytest.mark.parametrize('scene,text,available', [
    ('cooking_setup', '0/100', 0), ('cooking_setup', '3/100', 3),
    ('cooking_setup', '0/1O', None), ('home', '0/100', None),
])
def test_post_round_energy_records_observed_balance_and_saves_zero_only(scene, text, available):
    events = []
    nav = SimpleNamespace(text=lambda *args: [SimpleNamespace(text=text)], save=lambda *args: 'zero.png',
                          recording=SimpleNamespace(mark=lambda event, **data: events.append((event, data))))
    record_post_round_energy(nav, Observation(None, scene, 0), task='cooking', round_number=2)
    assert events[0][0] == 'energy_balance_after_round'
    assert events[0][1]['resource']['available'] == available
    assert events[0][1]['evidence'] == ('zero.png' if available == 0 else None)


def test_insufficient_budget_cancels_energy_settings_without_confirming():
    calls = []
    nav = SimpleNamespace(open_energy_settings=lambda task: object(),
                          cancel_energy_settings=lambda: calls.append('cancel'),
                          click_text=lambda *a, **k: calls.append('confirm'))
    settings = EnergySettings(nav)
    settings.read = lambda observation: EnergySelection(2, 60, ResourceObservation(100, 200))
    settings.wait_read = lambda ob: (ob, settings.read(ob))
    with pytest.raises(ResourceUnavailable):
        settings.configure('cooking', 2, budget_remaining=59)
    assert calls == ['cancel']


def test_paid_capacity_and_explicit_budget_are_used_together():
    calls = []
    nav = SimpleNamespace(open_energy_settings=lambda task: object(),
                          click_text=lambda *a, **k: calls.append('confirm'),
                          text=lambda *a, **k: [SimpleNamespace(text='×120×5', box=(850, 590, 150, 30))])
    settings = EnergySettings(nav)
    settings.read = lambda observation: EnergySelection(4, 120, ResourceObservation(180, 200))
    settings.wait_read = lambda ob: (ob, settings.read(ob))
    assert settings.configure('cooking', 4, budget_remaining=120).cost == 120
    assert calls == ['confirm']


def test_observed_zero_cost_can_be_confirmed_with_zero_budget_and_no_energy():
    calls = []
    nav = SimpleNamespace(open_energy_settings=lambda task: object(),
                          click_text=lambda *a, **k: calls.append('confirm'),
                          text=lambda *a, **k: [SimpleNamespace(text='×0', box=(850, 590, 150, 30))])
    settings = EnergySettings(nav)
    settings.wait_read = lambda ob: (ob, EnergySelection(1, 0, ResourceObservation(0, 20)))
    assert settings.configure('solo_live', 1, budget_remaining=0).cost == 0
    assert calls == ['confirm']


def test_explicit_empty_energy_policy_uses_zero_effective_cost_without_changing_nominal_setting():
    calls = []
    nav = SimpleNamespace(open_energy_settings=lambda task: object(),
                          click_text=lambda *a, **k: calls.append('confirm'),
                          text=lambda ob, expected, roi: [SimpleNamespace(
                              text='0/20' if roi == (1025, 25, 145, 60) else '×0', box=(850, 590, 150, 30))])
    settings = EnergySettings(nav)
    settings.wait_read = lambda ob: (ob, EnergySelection(1, 1, ResourceObservation(0, 20), True))
    selection = settings.configure('solo_live', 1, budget_remaining=0)
    assert selection.cost == 1 and selection.effective_cost == 0
    assert calls == ['confirm']


@pytest.mark.parametrize('multiplier,zeros,allowed', [('0',['0'],True), ('1',['0'],False),
                                                   ('0',[],False), ('0',['O'],False),
                                                   ('0',['0','0'],False)])
def test_missing_large_cost_requires_positive_unique_zero_in_its_own_crop(multiplier, zeros, allowed):
    def text(observation, expected, roi, **kwargs):
        if roi == (1115,339,92,65): words = [multiplier]
        elif roi == (998,555,50,50): words = zeros
        elif roi == (1025,25,145,60): words = ['53/100']
        else: words = []
        return [SimpleNamespace(text=word) for word in words]
    settings = EnergySettings(SimpleNamespace(text=text))
    ob = Observation(np.zeros((720,1280,3), dtype=np.uint8), 'energy_settings', 0)
    if allowed:
        value = settings.read(ob)
        assert value.multiplier == value.cost == 0 and value.resource.available == 53
    else:
        with pytest.raises(NavigationError): settings.read(ob)


@pytest.mark.parametrize('multiplier,cost', [('1', '1'), ('9', '9'), ('10', '10'), ('99', '99'),
                                          ('×2', '60'), ('x10', '10')])
def test_multiplier_crop_includes_leading_digit_of_two_digit_values(multiplier, cost):
    rois = []
    def text(observation, expected, roi, **kwargs):
        rois.append(roi)
        words = [multiplier] if roi == (1115, 339, 92, 65) else (
            [cost] if roi == (975, 545, 100, 70) else ['2/10'])
        return [SimpleNamespace(text=word) for word in words]
    selection = EnergySettings(SimpleNamespace(text=text)).read(
        Observation(np.zeros((720, 1280, 3), dtype=np.uint8), 'energy_settings', 0))
    assert selection.multiplier == int(multiplier.lstrip('×xX')) and selection.cost == int(cost)
    assert rois[0] == (1115, 339, 92, 65)


def test_truncated_ten_cannot_be_treated_as_a_positive_cost_zero_multiplier():
    def text(observation, expected, roi, **kwargs):
        word = '0' if roi == (1115, 339, 92, 65) else '10' if roi == (975, 545, 100, 70) else '2/10'
        return [SimpleNamespace(text=word)]
    settings = EnergySettings(SimpleNamespace(text=text))
    with pytest.raises(NavigationError, match='矛盾'):
        settings.read(Observation(np.zeros((720, 1280, 3), dtype=np.uint8), 'energy_settings', 0))


@pytest.mark.parametrize('notes,expected', [(['能量不足時，將消耗目前的能量'], 2),
    ([], 5), (['其他提示'], 5), (['能量不足時將消耗目前的能量']*2, 5)])
def test_positive_shortage_requires_the_exact_observed_policy_before_using_remaining_energy(notes, expected):
    def text(observation, pattern, roi, **kwargs):
        words = ['5'] if roi in {(1115, 339, 92, 65), (975, 545, 100, 70)} else (
            ['2/10'] if roi == (1025, 25, 145, 60) else notes)
        return [SimpleNamespace(text=word) for word in words]
    selection = EnergySettings(SimpleNamespace(text=text)).read(
        Observation(np.zeros((720, 1280, 3), dtype=np.uint8), 'energy_settings', 0))
    assert selection.effective_cost == expected and not selection.empty_energy_play
    assert selection.partial_energy_play == (expected == 2)


@pytest.mark.parametrize('displayed,balance,allowed', [(5, 2, True), (2, 2, True), (5, 3, False), (4, 2, False)])
def test_partial_energy_confirmation_keeps_the_observed_balance_and_accepts_only_its_nominal_or_effective_cost(displayed, balance, allowed):
    calls = []
    def text(observation, pattern, roi):
        word = f'{balance}/10' if roi == (1025, 25, 145, 60) else str(displayed)
        return [SimpleNamespace(text=word)]
    nav = SimpleNamespace(open_energy_settings=lambda task: object(),
        text=text, save=lambda *a: 'mismatch.png',
        click_text=lambda *a, **kw: calls.append('confirm') or SimpleNamespace(scene='solo_setup'))
    settings = EnergySettings(nav)
    settings.wait_read = lambda ob: (ob, EnergySelection(5, 5, ResourceObservation(2, 10), partial_energy_play=True))
    if allowed:
        assert settings.configure('solo_live', 5, 2).effective_cost == 2
    else:
        with pytest.raises(NavigationError): settings.configure('solo_live', 5, 2)
    assert calls == ['confirm']  # This only changes settings, never starts a song.
