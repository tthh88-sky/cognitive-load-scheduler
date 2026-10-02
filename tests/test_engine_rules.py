from datetime import date, datetime, time

from app.preferences import ENERGY_PRESETS, Preferences
from app.scheduler import EventIn, TaskIn, generate_plan
from tests.test_engine_core import assert_no_overlaps

WEEK_START = date(2026, 1, 5)  # Monday
FLAT = Preferences(energy_curve=ENERGY_PRESETS["flat"])


def test_deep_cap():
    tasks = [TaskIn(i, f"t{i}", 8, 180, None, "Medium") for i in (1, 2, 3)]
    assignments = generate_plan(tasks, [], WEEK_START, prefs=FLAT)
    assert_no_overlaps(assignments, [])
    days = {a.task_id: a.day_of_week for a in assignments}
    assert days == {1: "Mon", 2: "Tue", 3: "Wed"}
    assert all(a.slot_start == time(9, 0) for a in assignments)


def test_deep_break():
    tasks = [TaskIn(i, f"t{i}", 5, 60, None, "Medium") for i in (1, 2)]
    assignments = generate_plan(tasks, [], WEEK_START, prefs=FLAT)
    assert_no_overlaps(assignments, [])
    starts = sorted(a.slot_start for a in assignments)
    assert starts == [time(9, 0), time(10, 15)]


def test_urgent_may_exceed_cap():
    events = [
        EventIn(d, time(9, 0), time(19, 0), "meeting") for d in ("Tue", "Wed", "Thu", "Fri")
    ]
    tasks = [
        TaskIn(i, f"t{i}", 8, 180, datetime(2026, 1, 5, 19, 0), "High") for i in (1, 2)
    ]
    assignments = generate_plan(tasks, events, WEEK_START, prefs=FLAT)
    assert_no_overlaps(assignments, events)
    starts = sorted((a.slot_start, a.slot_end) for a in assignments)
    assert starts == [(time(9, 0), time(12, 0)), (time(12, 15), time(15, 15))]
    second = next(a for a in assignments if a.slot_start == time(12, 15))
    assert "deep_cap" in second.factors["relaxed"]
    assert any("cap" in w for w in second.warnings)


def _interruptible_calendar():
    return [
        EventIn("Mon", time(9, 0), time(14, 0), "meeting"),
        EventIn("Mon", time(14, 0), time(16, 0), "other", interruptible=True, title="Office hours"),
        EventIn("Mon", time(16, 0), time(19, 0), "meeting"),
        *[EventIn(d, time(9, 0), time(19, 0), "meeting") for d in ("Tue", "Wed", "Thu", "Fri")],
    ]


def test_interruptible_normal_blocked():
    events = _interruptible_calendar()
    tasks = [TaskIn(1, "x", 3, 60, None, "Medium")]
    assignments = generate_plan(tasks, events, WEEK_START)
    assert assignments[0].unschedulable is True


def test_interruptible_urgent_overflow():
    events = _interruptible_calendar()
    tasks = [TaskIn(1, "x", 3, 60, datetime(2026, 1, 5, 18, 0), "High")]
    assignments = generate_plan(tasks, events, WEEK_START)
    a = assignments[0]
    assert not a.unschedulable
    assert a.day_of_week == "Mon"
    assert time(14, 0) <= a.slot_start < time(16, 0)
    assert "overlaps_interruptible" in a.factors["relaxed"]
    assert a.override_reason is not None


def test_min_split_session():
    events = [
        EventIn("Mon", time(9, 20), time(11, 0), "meeting"),
        EventIn("Mon", time(11, 40), time(19, 0), "meeting"),
    ]
    tasks = [TaskIn(1, "x", 5, 60, None, "Medium")]
    assignments = generate_plan(tasks, events, WEEK_START, allow_split=True)
    assert_no_overlaps(assignments, events)
    sessions = sorted((a.day_of_week, a.slot_start, a.slot_end) for a in assignments)
    assert sessions == [("Mon", time(11, 5), time(11, 35)), ("Tue", time(9, 0), time(9, 30))]


def test_afternoon_window():
    tasks = [TaskIn(1, "x", 13, 120, None, "Medium", time_window="afternoon")]
    assignments = generate_plan(tasks, [], WEEK_START)
    a = assignments[0]
    assert (a.day_of_week, a.slot_start, a.slot_end) == ("Mon", time(15, 0), time(17, 0))


def test_not_before():
    tasks = [TaskIn(1, "x", 1, 15, None, "Medium", not_before=datetime(2026, 1, 6, 14, 0))]
    assignments = generate_plan(tasks, [], WEEK_START)
    a = assignments[0]
    assert (a.day_of_week, a.slot_start) == ("Tue", time(14, 0))


def test_reason_text():
    tasks = [TaskIn(1, "x", 8, 120, None, "Medium")]
    assignments = generate_plan(tasks, [], WEEK_START, prefs=FLAT)
    a = assignments[0]
    assert a.reasoning_text == (
        "Mon 09:00–11:00 · peak energy, no recent events (focus 1.00) "
        "· Mon deep work 2 h of 4 h"
    )


def test_unschedulable_longest_window():
    events = []
    for d in ("Mon", "Tue", "Wed", "Thu", "Fri"):
        events.append(EventIn(d, time(9, 0), time(12, 0), "meeting"))
        events.append(EventIn(d, time(14, 0), time(19, 0), "meeting"))
    tasks = [TaskIn(1, "x", 5, 180, None, "Medium")]
    assignments = generate_plan(tasks, events, WEEK_START)
    a = assignments[0]
    assert a.unschedulable
    assert "115 min" in a.reasoning_text


def test_unschedulable_deadline_already_passed():
    tasks = [TaskIn(1, "x", 3, 60, datetime(2026, 1, 5, 12, 0), "Medium")]
    now = datetime(2026, 1, 6, 9, 0)  # Tue; the Mon deadline is behind us
    assignments = generate_plan(tasks, [], WEEK_START, now=now)
    a = assignments[0]
    assert a.unschedulable
    assert "already passed" in a.reasoning_text
    assert "Jan 05" in a.reasoning_text


def test_unschedulable_cap():
    prefs = Preferences(max_deep_minutes_per_day=60)
    tasks = [TaskIn(1, "x", 8, 120, None, "Medium")]
    assignments = generate_plan(tasks, [], WEEK_START, prefs=prefs)
    a = assignments[0]
    assert a.unschedulable
    assert "60-min cap" in a.reasoning_text
