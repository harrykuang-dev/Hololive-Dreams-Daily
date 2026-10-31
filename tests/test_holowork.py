from types import SimpleNamespace

import pytest

from daily import holowork
from daily.holowork import HoloWorkRunner, WorkView, button_map
from daily.navigation import NavigationError


BUTTONS = {name: (i * 100, 620, 80, 30) for i, name in enumerate(
    ('全部領取', '領取', '點擊關閉', '一鍵設定', '推薦設定', '決定'))}


def view(kind, *, scores=(), empty=()):
    return WorkView(SimpleNamespace(scene='holowork', image=None), kind, BUTTONS,
                    scores, empty, ('07:59:59',) * 3 if kind == 'working' else ())


def runner_for(views, monkeypatch):
    clicks, saved = [], []
    navigator = SimpleNamespace(inputs=SimpleNamespace(click=lambda x, y: clicks.append((x, y)),
                                                       pause=lambda duration: None),
                                save=lambda observation, name: saved.append(name) or name + '.png')
    runner = HoloWorkRunner(navigator)
    remaining = iter(views)
    last = views[-1]
    runner.read = lambda *args: next(remaining, last)
    tick = iter(i * .25 for i in range(10000))
    monkeypatch.setattr(holowork.time, 'monotonic', lambda: next(tick))
    return runner, clicks, saved


def test_complete_collection_dispatch_and_timer_verification(monkeypatch):
    assignment = view('assignment', scores=(100, 200, 300), empty=(0, 0, 0))
    runner, clicks, saved = runner_for([
        view('completed'), view('summary'), view('get'), view('available'),
        view('assignment', scores=(None,) * 3, empty=(5,) * 3),
        assignment, assignment, assignment, assignment, assignment,
        view('started'), view('working')], monkeypatch)
    result = runner.run()
    assert result.status == 'complete' and result.completed_rounds == 1
    assert clicks == [(BUTTONS[name][0] + 40, 635) for name in
                      ('全部領取', '領取', '點擊關閉', '一鍵設定', '推薦設定', '決定')]
    assert result.evidence == 'holowork-dispatched.png'
    assert 'holowork-started' in saved


def test_partial_team_is_preserved_without_recommend_or_dispatch(monkeypatch):
    runner, clicks, _ = runner_for([
        view('assignment', scores=(100, None, 300), empty=(0, 5, 0))], monkeypatch)
    with pytest.raises(NavigationError, match='部分'):
        runner.run()
    assert not clicks


def test_unknown_state_with_same_header_cannot_confirm_working(monkeypatch):
    runner, clicks, saved = runner_for([view('unknown')], monkeypatch)
    with pytest.raises(NavigationError, match='逾時'):
        runner.run()
    assert not clicks and saved == ['holowork-stalled']


def test_dispatch_is_not_retried_when_confirmation_is_missing(monkeypatch):
    assignment = view('assignment', scores=(100, 200, 300), empty=(0, 0, 0))
    runner, clicks, _ = runner_for([assignment, view('unknown')], monkeypatch)
    runner.stable_assignment = lambda view: view
    with pytest.raises(NavigationError, match='逾時'):
        runner.run()
    assert clicks == [(BUTTONS['決定'][0] + 40, 635)]


def test_changing_team_totals_must_settle_before_dispatch(monkeypatch):
    stable = view('assignment', scores=(101, 200, 300), empty=(0, 0, 0))
    runner, clicks, _ = runner_for([
        view('assignment', scores=(99, 200, 300), empty=(0, 0, 0)),
        stable, stable, stable, stable, stable], monkeypatch)
    result = runner.stable_assignment(view('assignment', scores=(98, 200, 300), empty=(0, 0, 0)))
    assert result.scores == stable.scores
    assert not clicks


def test_existing_work_requires_no_input(monkeypatch):
    runner, clicks, _ = runner_for([view('working')], monkeypatch)
    result = runner.run()
    assert result.status == 'complete' and result.completed_rounds == 0
    assert not clicks


def test_bulk_claim_does_not_become_an_individual_claim_button():
    items = [SimpleNamespace(text='全部領取', box=(0, 0, 80, 20))]
    assert set(button_map(items)) == {'全部領取'}
    items.append(SimpleNamespace(text='全部領取', box=(100, 0, 80, 20)))
    assert not button_map(items)


def test_observed_bulk_setup_ocr_variant_remains_a_unique_button():
    items = [SimpleNamespace(text='键設定', box=(1000, 620, 80, 30))]
    assert button_map(items) == {'一鍵設定': (1000, 620, 80, 30)}
