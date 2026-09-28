from datetime import date, datetime, time

from app.preferences import ENERGY_PRESETS, Preferences
from app.scheduler import EventIn, TaskIn, compute_urgency, generate_plan
from app.scheduler import _day_residue, _residue_at

WEEK_START = date(2026, 1, 5)  # Monday
DEFAULT_NOW = datetime.combine(WEEK_START, time.min)


def assert_no_overlaps(assignments, events):
    by_day = {}
    for e in events:
        by_day.setdefault(e.day_of_week, []).append((_m(e.start_time), _m(e.end_time)))
    for a in assignments:
        if a.unschedulable:
            continue
        by_day.setdefault(a.day_of_week, []).append((_m(a.slot_start), _m(a.slot_end)))

    for day, intervals in by_day.items():
        ordered = sorted(intervals)
        for (s1, e1), (s2, e2) in zip(ordered, ordered[1:]):
            assert e1 <= s2, f"overlap on {day}: {(s1,e1)} vs {(s2,e2)}"


def _m(t: time) -> int:
    return t.hour * 60 + t.minute


def test_past_is_skipped():
    tasks = [TaskIn(1, "Quick", 1, 15, None, "Medium")]
    now = datetime(2026, 1, 7, 13, 0)  # Wed 13:00
    assignments = generate_plan(tasks, [], WEEK_START, now=now)
    assert_no_overlaps(assignments, [])
    a = assignments[0]
    assert (a.day_of_week, a.slot_start) == ("Wed", time(13, 0))


def test_deadline_uses_task_end():
    tasks = [TaskIn(1, "Bug", 3, 60, datetime(2026, 1, 5, 12, 0), "High")]
    assignments = generate_plan(tasks, [], WEEK_START)
    assert_no_overlaps(assignments, [])
    a = assignments[0]
    assert (a.day_of_week, a.slot_start, a.slot_end) == ("Mon", time(9, 0), time(10, 0))


def test_non_urgent_deadline_respected():
    events = [
        EventIn("Mon", time(9, 0), time(10, 0), "meeting"),
        EventIn("Mon", time(10, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(11, 0), time(12, 0), "meeting"),
    ]
    tasks = [TaskIn(1, "Report", 13, 120, datetime(2026, 1, 5, 17, 0), "Low")]
    assignments = generate_plan(tasks, events, WEEK_START)
    assert_no_overlaps(assignments, events)
    a = assignments[0]
    assert a.override_reason is None
    assert (a.day_of_week, a.slot_start, a.slot_end) == ("Mon", time(15, 0), time(17, 0))
    assert a.factors["cost"] == 0.2501


def test_urgency_from_today():
    task = TaskIn(1, "x", 2, 60, datetime(2026, 1, 9), "Medium")  # Friday
    today = date(2026, 1, 8)  # Thursday
    assert compute_urgency(task, today) == 4


def test_residue_decays():
    prefs = Preferences()
    events = [EventIn("Mon", time(9, 0), time(10, 0), "meeting")]
    residue = _day_residue(events, prefs)
    r, _ = _residue_at(10 * 60 + 20, residue, prefs.residue_half_life_minutes)
    assert round(r, 3) == 0.075


def test_stacking_needs_back_to_back():
    prefs = Preferences()
    gapped = [
        EventIn("Mon", time(9, 0), time(10, 0), "meeting"),
        EventIn("Mon", time(14, 0), time(15, 0), "meeting"),
    ]
    residue = _day_residue(gapped, prefs)
    assert residue[0][1] == 0.15
    assert residue[1][1] == 0.15  # far apart: no stacking bonus, still base 0.15


def test_stacking_chain():
    prefs = Preferences()
    events = [
        EventIn("Mon", time(9, 0), time(10, 0), "meeting"),
        EventIn("Mon", time(10, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(11, 0), time(12, 0), "meeting"),
    ]
    residue = _day_residue(events, prefs)
    r0s = [round(r0, 2) for _ev, r0 in residue]
    assert r0s == [0.15, 0.25, 0.35]

    r1, _ = _residue_at(12 * 60 + 20, residue, prefs.residue_half_life_minutes)
    r2, _ = _residue_at(12 * 60 + 40, residue, prefs.residue_half_life_minutes)
    r3, _ = _residue_at(13 * 60, residue, prefs.residue_half_life_minutes)
    assert round(r1, 3) == 0.175
    assert round(r2, 4) == 0.0875
    assert round(r3, 4) == round(0.35 * 2 ** (-60 / 20), 4)


def test_heavy_waits_light_fills():
    flat_prefs = Preferences(energy_curve=ENERGY_PRESETS["flat"])
    events = [
        EventIn("Mon", time(9, 0), time(10, 0), "meeting"),
        EventIn("Mon", time(10, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(11, 0), time(12, 0), "meeting"),
    ]
    tasks = [
        TaskIn(1, "heavy", 13, 60, None, "Medium"),
        TaskIn(2, "light", 1, 15, None, "Medium"),
    ]
    assignments = generate_plan(tasks, events, WEEK_START, prefs=flat_prefs)
    assert_no_overlaps(assignments, events)
    heavy = next(a for a in assignments if a.task_id == 1)
    light = next(a for a in assignments if a.task_id == 2)
    assert heavy.slot_start == time(12, 45)
    assert light.slot_start == time(12, 5)


def test_energy_curve_respected():
    afternoon = Preferences(energy_curve=ENERGY_PRESETS["afternoon"])
    tasks = [TaskIn(1, "x", 13, 120, None, "Medium")]
    assignments = generate_plan(tasks, [], WEEK_START, prefs=afternoon)
    a = assignments[0]
    assert a.day_of_week == "Mon"
    assert a.slot_start == time(14, 15)
    assert a.factors["mean_focus"] >= 0.95


def test_deterministic():
    events = [EventIn("Mon", time(9, 0), time(10, 0), "meeting")]
    tasks = [TaskIn(1, "a", 5, 60, None, "Medium"), TaskIn(2, "b", 8, 90, None, "High")]
    a1 = generate_plan(tasks, events, WEEK_START)
    a2 = generate_plan(tasks, events, WEEK_START)
    key = lambda a: (a.task_id, a.day_of_week, a.slot_start, a.slot_end, a.reasoning_text)
    assert [key(a) for a in a1] == [key(a) for a in a2]
