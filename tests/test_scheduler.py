from datetime import date, datetime, time

from app.scheduler import (
    DAY_ORDER,
    EventIn,
    TaskIn,
    compute_urgency,
    extract_free_slots,
    generate_plan,
)


def test_extract_free_slots_basic_gap():
    events = [
        EventIn("Mon", time(10, 0), time(11, 0), "meeting"),
    ]
    slots = extract_free_slots(events)
    mon_slots = [s for s in slots if s.day_of_week == "Mon"]
    assert mon_slots[0].start == time(9, 0)
    assert mon_slots[0].end == time(10, 0)
    assert mon_slots[0].residue_penalty == 0.0  # nothing precedes the first gap
    assert mon_slots[1].start == time(11, 0)
    assert mon_slots[1].end == time(19, 0)
    assert mon_slots[1].residue_penalty == 0.15  # inherits meeting penalty


def test_consecutive_meeting_bonus_caps_at_three():
    events = [
        EventIn("Tue", time(9, 0), time(10, 0), "meeting"),
        EventIn("Tue", time(10, 0), time(11, 0), "meeting"),
        EventIn("Tue", time(11, 0), time(12, 0), "meeting"),
        EventIn("Tue", time(12, 0), time(13, 0), "meeting"),
    ]
    slots = extract_free_slots(events)
    after = [s for s in slots if s.day_of_week == "Tue" and s.start == time(13, 0)][0]
    # base 0.15 + bonus 0.10*(3-1) = 0.35, capped well under 0.6
    assert round(after.residue_penalty, 2) == 0.35


def test_penalty_clamped_to_0_6_with_many_stacked_meetings():
    events = [EventIn("Wed", time(h, 0), time(h + 1, 0), "meeting") for h in range(9, 17)]
    slots = extract_free_slots(events)
    last = [s for s in slots if s.day_of_week == "Wed"][-1]
    assert last.residue_penalty <= 0.6


def test_on_site_has_moderate_penalty():
    events = [EventIn("Thu", time(9, 0), time(10, 0), "on_site")]
    slots = extract_free_slots(events)
    after = [s for s in slots if s.day_of_week == "Thu" and s.start == time(10, 0)][0]
    assert after.residue_penalty == 0.10


def test_urgency_high_priority_near_deadline():
    ref = date(2026, 1, 5)  # Monday
    task = TaskIn(1, "x", 5, 60, datetime(2026, 1, 6), "High")
    assert compute_urgency(task, ref) == 3 + 2  # High + <=2 days out


def test_generate_plan_places_high_load_in_best_slot():
    events = [
        EventIn("Mon", time(9, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(11, 0), time(12, 0), "on_site"),
    ]
    tasks = [
        TaskIn(1, "Design new schema", 8, 180, None, "Medium"),
        TaskIn(2, "Small bug", 2, 30, None, "Low"),
    ]
    week_start = date(2026, 1, 5)  # Monday
    assignments = generate_plan(tasks, events, week_start)
    schema_assignment = next(a for a in assignments if a.task_id == 1)
    assert not schema_assignment.unschedulable
    assert schema_assignment.override_reason is None


def test_generate_plan_overrides_for_urgent_task_in_high_residue_slot():
    events = [
        EventIn("Mon", time(9, 0), time(10, 0), "meeting"),
        EventIn("Mon", time(10, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(11, 0), time(12, 0), "meeting"),
    ]
    week_start = date(2026, 1, 5)
    tasks = [
        TaskIn(1, "Fix bug A", 3, 60, datetime(2026, 1, 5, 13, 10), "High"),
    ]
    assignments = generate_plan(tasks, events, week_start)
    a = assignments[0]
    assert not a.unschedulable
    assert a.override_reason is not None
    assert (a.day_of_week, a.slot_start, a.slot_end) == ("Mon", time(12, 5), time(13, 5))
    assert a.residue_penalty_applied == 0.29


def test_generate_plan_flags_unschedulable_task():
    events = [
        EventIn(day, time(9, 0), time(19, 0), "meeting")
        for day in DAY_ORDER
    ]
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Design new webpage", 8, 180, datetime(2026, 1, 7), "High")]
    assignments = generate_plan(tasks, events, week_start)
    assert assignments[0].unschedulable is True


def test_quality_first_avoids_residue_when_a_clean_alternative_fits():
    # Mon: meeting 9-11, lunch 12-13 -> two candidate slots: an 11:00-12:00
    # sliver carrying meeting residue, and a clean 13:00-19:00 block. A
    # load-13 task should skip the residue-carrying slot for the clean one,
    # even though it's much bigger (no size penalty -- see `_fit_score`).
    from app.preferences import ENERGY_PRESETS, Preferences

    events = [
        EventIn("Mon", time(9, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(12, 0), time(13, 0), "lunch"),
    ]
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Deep work", 13, 45, None, "Medium")]
    flat_prefs = Preferences(energy_curve=ENERGY_PRESETS["flat"])
    assignments = generate_plan(tasks, events, week_start, prefs=flat_prefs)
    a = assignments[0]
    assert a.day_of_week == "Mon"
    assert a.slot_start == time(13, 0)
    assert a.residue_penalty_applied == 0.0


def test_trivial_task_fills_a_nearby_mild_residue_slot():
    # A load-1 task's weighted residue for a standard meeting (0.15 * 1/13
    # ~= 0.012) is below MIN_SIGNIFICANT_RESIDUE_SCORE, so it's treated as
    # noise: the task falls through to the (day, start) tiebreaker and
    # fills the nearby 11:00 gap rather than reaching across to the bigger
    # zero-residue afternoon block. This keeps good-enough slots from
    # sitting empty just because a technically-cleaner slot exists elsewhere.
    events = [
        EventIn("Mon", time(9, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(12, 0), time(13, 0), "lunch"),
    ]
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Typo fix", 1, 15, None, "Medium")]
    assignments = generate_plan(tasks, events, week_start)
    a = assignments[0]
    assert a.slot_start == time(11, 5)
    assert a.residue_penalty_applied == 0.13


def test_higher_load_task_still_avoids_the_same_residue_slot():
    # Same calendar, but load 5 (0.15 * 5/13 ~= 0.058) clears the
    # significance threshold, so residue-avoidance still applies normally.
    events = [
        EventIn("Mon", time(9, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(12, 0), time(13, 0), "lunch"),
    ]
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Small feature tweak", 8, 15, None, "Medium")]
    assignments = generate_plan(tasks, events, week_start)
    a = assignments[0]
    assert (a.day_of_week, a.slot_start) == ("Mon", time(11, 30))


def test_quality_first_breaks_residue_ties_by_earliest_day():
    # Mon: meeting 9-11, lunch 12-13 -> 13:00-19:00 is zero-residue (360min).
    # Tue: lunch 12-13 only -> 09:00-12:00 is also zero-residue (180min).
    # Both slots fit a 180min task with identical (zero) residue score, so
    # the tie should resolve to the earlier day rather than the smaller slot.
    from app.preferences import ENERGY_PRESETS, Preferences

    events = [
        EventIn("Mon", time(9, 0), time(11, 0), "meeting"),
        EventIn("Mon", time(12, 0), time(13, 0), "lunch"),
        EventIn("Tue", time(12, 0), time(13, 0), "lunch"),
    ]
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Design new schema", 8, 180, None, "Medium")]
    flat_prefs = Preferences(energy_curve=ENERGY_PRESETS["flat"])
    assignments = generate_plan(tasks, events, week_start, prefs=flat_prefs)
    a = assignments[0]
    assert a.day_of_week == "Mon"
    assert a.slot_start == time(13, 0)


def _split_calendar():
    # Mon: meeting 11:00-15:00 and 16:30-19:00 -> two free slots that
    # neither individually fit a 180min task: 09:00-11:00 (120min) and
    # 15:00-16:30 (90min, carrying meeting residue). Combined: 210min.
    # Tue-Fri are fully booked so Monday's fragments are the only capacity
    # in the week -- otherwise the normal matcher would just use one of them.
    return [
        EventIn("Mon", time(11, 0), time(15, 0), "meeting"),
        EventIn("Mon", time(16, 30), time(19, 0), "meeting"),
        *[EventIn(d, time(9, 0), time(19, 0), "meeting") for d in ["Tue", "Wed", "Thu", "Fri"]],
    ]


def test_split_disabled_by_default_leaves_task_unschedulable():
    events = _split_calendar()
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Deep work block", 5, 180, None, "Medium")]
    assignments = generate_plan(tasks, events, week_start)  # allow_split defaults False
    assert assignments[0].unschedulable is True


def test_split_covers_task_across_two_slots_when_enabled():
    # Eager splitting walks slots chronologically (day, then time), not by
    # residue -- Monday's earlier 09:00-11:00 slot is consumed first here
    # simply because it comes first, not because it's cleaner.
    events = _split_calendar()
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Deep work block", 5, 180, None, "Medium")]
    assignments = generate_plan(tasks, events, week_start, allow_split=True)

    assert len(assignments) == 2
    assert all(a.task_id == 1 and not a.unschedulable for a in assignments)
    assert {a.split_label for a in assignments} == {"1/2", "2/2"}
    assert sum(
        (a.slot_end.hour * 60 + a.slot_end.minute) - (a.slot_start.hour * 60 + a.slot_start.minute)
        for a in assignments
    ) == 180
    first, second = sorted(assignments, key=lambda a: a.slot_start)
    assert (first.day_of_week, first.slot_start, first.slot_end) == ("Mon", time(9, 0), time(11, 0))
    assert (second.day_of_week, second.slot_start, second.slot_end) == ("Mon", time(15, 5), time(16, 5))


def test_split_still_unschedulable_if_combined_capacity_insufficient():
    # Same calendar, but task needs more than the 210min Monday can offer combined.
    events = _split_calendar()
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Huge task", 13, 240, None, "Medium")]
    assignments = generate_plan(tasks, events, week_start, allow_split=True)
    assert len(assignments) == 1
    assert assignments[0].unschedulable is True


def test_split_respects_urgent_deadline_on_every_session():
    # Urgent task (High + deadline tomorrow) that only fits by splitting
    # across Monday's two fragments before its deadline.
    events = _split_calendar()
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Urgent deep work", 5, 180, datetime(2026, 1, 6, 12, 0), "High")]
    assignments = generate_plan(tasks, events, week_start, allow_split=True)
    assert len(assignments) == 2
    assert all(not a.unschedulable for a in assignments)
    assert all(a.day_of_week == "Mon" for a in assignments)


def _cross_day_calendar():
    # Mon: fully booked (irrelevant to the scenario).
    # Tue: free only 16:00-18:00 (120min).
    # Wed: fully free 09:00-19:00 (600min) -- big enough to hold the whole
    # 180min task alone, so the quality-first match would happily wait for
    # Wednesday instead of using Tuesday's smaller window first.
    return [
        EventIn("Mon", time(9, 0), time(19, 0), "meeting"),
        EventIn("Tue", time(9, 0), time(16, 0), "meeting"),
        EventIn("Tue", time(18, 0), time(19, 0), "meeting"),
    ]


def test_split_starts_on_tuesday_instead_of_waiting_for_wednesday():
    # Reported scenario: Tue 16:00-18:00 free, Wed free all day. With
    # splitting on, the task should start on Tuesday's smaller window and
    # finish first thing Wednesday, rather than waiting for Wednesday's
    # single big block even though that would also fit whole.
    events = _cross_day_calendar()
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Big task", 5, 180, None, "Medium")]
    assignments = generate_plan(tasks, events, week_start, allow_split=True)

    assert len(assignments) == 2
    assert {a.split_label for a in assignments} == {"1/2", "2/2"}
    first, second = sorted(assignments, key=lambda a: (DAY_ORDER.index(a.day_of_week), a.slot_start))
    assert (first.day_of_week, first.slot_start, first.slot_end) == ("Tue", time(16, 5), time(18, 0))
    assert (second.day_of_week, second.slot_start, second.slot_end) == ("Wed", time(9, 0), time(10, 5))


def test_without_split_waits_for_wednesdays_whole_block():
    # Same calendar, splitting off: Tuesday's 120min window doesn't fit the
    # 180min task on its own, so quality-first matching should skip straight
    # to Wednesday's single big block instead of ever considering Tuesday.
    events = _cross_day_calendar()
    week_start = date(2026, 1, 5)
    tasks = [TaskIn(1, "Big task", 5, 180, None, "Medium")]
    assignments = generate_plan(tasks, events, week_start)

    assert len(assignments) == 1
    a = assignments[0]
    assert a.split_label is None
    assert (a.day_of_week, a.slot_start, a.slot_end) == ("Wed", time(9, 0), time(12, 0))
