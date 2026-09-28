from app.llm.estimator import HistoryExample, build_messages, estimate
from app.llm.provider import FakeProvider


def test_build_messages_has_all_scale_lines():
    system, user = build_messages("Fix bug", "desc", "", "Backend dev", [])
    for anchor in ["Trivial fix", "Small bug", "Bug fix requiring", "Feature work", "New schema", "New subsystem"]:
        assert anchor in system
    assert "Backend dev" in user
    assert "Past tasks: none yet" in user
    assert "Clarification:" not in user


def test_build_messages_includes_clarification_only_when_answered():
    _, user = build_messages("Fix bug", "desc", "It's the login flow", "", [])
    assert "Clarification: It's the login flow" in user


def test_build_messages_includes_history_examples():
    examples = [
        HistoryExample("Fix login redirect loop", 3, 60, 95, "harder"),
    ]
    _, user = build_messages("New bug", "", "", "", examples)
    assert "Fix login redirect loop | 3 | 60 | 95 | harder" in user


def test_estimate_clamps_and_rounds_duration():
    provider = FakeProvider(
        ['{"rationale":"x","cognitive_load":1,"estimated_duration_minutes":3,"confidence":"high","clarifying_question":""}']
    )
    s = estimate(provider, "t", "", "", "", [])
    assert s.raw_duration_minutes == 5
    assert s.duration_minutes == 5

    provider = FakeProvider(
        ['{"rationale":"x","cognitive_load":13,"estimated_duration_minutes":999,"confidence":"high","clarifying_question":""}']
    )
    s = estimate(provider, "t", "", "", "", [])
    assert s.raw_duration_minutes == 480

    provider = FakeProvider(
        ['{"rationale":"x","cognitive_load":3,"estimated_duration_minutes":52,"confidence":"high","clarifying_question":""}']
    )
    s = estimate(provider, "t", "", "", "", [])
    assert s.raw_duration_minutes == 50


def test_estimate_applies_multiplier():
    provider = FakeProvider(
        ['{"rationale":"x","cognitive_load":5,"estimated_duration_minutes":60,"confidence":"high","clarifying_question":""}']
    )
    s = estimate(provider, "t", "", "", "", [], multiplier_for=lambda load: 1.3)
    assert s.duration_minutes == 80
    assert s.multiplier == 1.3
