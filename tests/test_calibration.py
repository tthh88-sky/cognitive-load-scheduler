from datetime import datetime, timedelta

from app.calibration import Outcome, duration_multiplier, hindsight_load, insights, select_examples


def _outcome(**kwargs):
    defaults = dict(
        title="Task",
        load=5,
        estimated_minutes=60,
        ai_load=None,
        ai_duration_minutes=None,
        actual_minutes=None,
        hindsight_load=None,
        load_source="manual",
        completed_at=datetime(2026, 1, 1),
    )
    defaults.update(kwargs)
    return Outcome(**defaults)


def test_hindsight_load_edges():
    assert hindsight_load(1, "easier") == 1
    assert hindsight_load(13, "harder") == 13
    assert hindsight_load(5, "harder") == 8


def test_multiplier_needs_five_samples():
    samples = [
        _outcome(actual_minutes=90, ai_duration_minutes=60, completed_at=datetime(2026, 1, i + 1))
        for i in range(4)
    ]
    assert duration_multiplier(samples) == 1.0


def test_multiplier_from_ratios():
    samples = [
        _outcome(actual_minutes=90, ai_duration_minutes=60, completed_at=datetime(2026, 1, i + 1))
        for i in range(5)
    ]
    assert duration_multiplier(samples) == 1.5

    samples3x = [
        _outcome(actual_minutes=300, ai_duration_minutes=100, completed_at=datetime(2026, 1, i + 1))
        for i in range(5)
    ]
    assert duration_multiplier(samples3x) == 2.0  # clamped from 3.0


def test_multiplier_bucket_preference():
    # 5 "deep" (load>=5) samples at ratio 2.0, plus a couple "light" ones at ratio 1.0.
    deep = [
        _outcome(load=8, actual_minutes=200, ai_duration_minutes=100, completed_at=datetime(2026, 1, i + 1))
        for i in range(5)
    ]
    light = [
        _outcome(load=2, actual_minutes=60, ai_duration_minutes=60, completed_at=datetime(2026, 1, i + 10))
        for i in range(2)
    ]
    assert duration_multiplier(deep + light, load=8) == 2.0
    # fewer than 5 in the requested bucket -> falls back to using all samples
    assert duration_multiplier(deep[:2] + light, load=2) == duration_multiplier(deep[:2] + light)


def test_select_examples_prefers_title_overlap():
    candidates = [
        _outcome(title=f"Unrelated task {i}", actual_minutes=30, completed_at=datetime(2026, 1, i + 1))
        for i in range(8)
    ]
    matching = [
        _outcome(title="Fix login redirect loop", actual_minutes=30, completed_at=datetime(2026, 1, 20)),
        _outcome(title="Investigate login timeout", actual_minutes=30, completed_at=datetime(2026, 1, 21)),
    ]
    result = select_examples(candidates + matching, "Fix login redirect bug", k=8)
    assert len(result) == 8
    titles = [o.title for o in result]
    assert "Fix login redirect loop" in titles
    assert "Investigate login timeout" in titles


def test_insights_counts():
    outcomes = [
        _outcome(estimated_minutes=60, actual_minutes=90, completed_at=datetime(2026, 1, 1)),
        _outcome(estimated_minutes=60, actual_minutes=60, completed_at=datetime(2026, 1, 2)),
        _outcome(ai_load=3, hindsight_load=3, completed_at=datetime(2026, 1, 3)),
        _outcome(ai_load=3, hindsight_load=5, completed_at=datetime(2026, 1, 4)),
        _outcome(load=5, load_source="manual", hindsight_load=5, completed_at=datetime(2026, 1, 5)),
    ]
    result = insights(outcomes)
    assert result.median_duration_error.samples == 2
    assert result.ai_match_rate.samples == 2
    assert result.ai_match_rate.value == 0.5
    assert result.ai_match_within_one_step.value == 1.0
    # outcomes[2] (load=5,hindsight=3), [3] (load=5,hindsight=5), [4] (load=5,hindsight=5)
    # all have load_source "manual" by default and a hindsight_load, so all three count.
    assert result.own_match_rate.samples == 3
    assert result.own_match_rate.value == 2 / 3
