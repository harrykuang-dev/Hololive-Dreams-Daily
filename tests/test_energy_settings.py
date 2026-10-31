from types import SimpleNamespace

import pytest

from daily.contracts import ResourceObservation
from daily.resources import EnergySelection, EnergySettings, ResourceUnavailable, parse_fraction


@pytest.mark.parametrize('text,available,capacity', [
    ('8/10+', 8, 10), ('100/200', 100, 200), (' 3 ／ 20 ', 3, 20),
    ('8/1O', 8, None), ('8/10++', 8, None), ('8/10/20', None, None),
    ('', None, None)])
def test_resource_fraction_keeps_numbers_and_rejects_ambiguous_ocr(text, available, capacity):
    assert parse_fraction(text) == ResourceObservation(available, capacity)


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
