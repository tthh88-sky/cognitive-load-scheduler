"""Cognitive-load aware scheduling engine.

Pure Python, framework-agnostic, no DB/web imports so it can be unit tested
in isolation. Operates on plain dataclasses, not ORM models. May import
app.preferences (also pure) but never app.llm, FastAPI or SQLAlchemy.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from app.preferences import WEEK, Preferences

DAY_ORDER = list(WEEK)

WORKDAY_START = time(9, 0)
WORKDAY_END = time(19, 0)

BASE_PENALTY_BY_TYPE = {
    "meeting": 0.15,
    "commute": 0.05,
    "on_site": 0.10,
    "lunch": -0.05,
    "other": 0.05,
}
CONSECUTIVE_MEETING_BONUS = 0.10
MAX_STACKED_BONUS_COUNT = 3
PENALTY_MIN = 0.0
PENALTY_MAX = 0.6

LOAD_DEFAULT_DURATION = {1: 15, 2: 30, 3: 60, 5: 120, 8: 180, 13: 240}
LOAD_ANCHOR = {
    1: "Trivial fix (typo, config value)",
    2: "Small bug, known repro steps",
    3: "Bug fix requiring investigation",
    5: "Feature work, moderate design decisions",
    8: "New schema/API design, cross-module changes",
    13: "New subsystem/architecture design",
}

PRIORITY_WEIGHT = {"Low": 1, "Medium": 2, "High": 3}
URGENT_THRESHOLD = 4

STEP_MINUTES = 15
TRANSITION_TYPES = {"meeting", "on_site", "commute"}
MIN_SIGNIFICANT_COST = 0.05


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _time_from_minutes(m: int) -> time:
    m = m % (24 * 60)
    return time(m // 60, m % 60)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _round_up_15(dt: datetime) -> datetime:
    total = dt.hour * 60 + dt.minute
    if dt.second == 0 and dt.microsecond == 0 and total % STEP_MINUTES == 0:
        return dt.replace(second=0, microsecond=0)
    rounded = ((total // STEP_MINUTES) + 1) * STEP_MINUTES
    day_overflow, minute_of_day = divmod(rounded, 24 * 60)
    base = datetime.combine(dt.date(), time(minute_of_day // 60, minute_of_day % 60))
    return base + timedelta(days=day_overflow)


@dataclass
class EventIn:
    day_of_week: str  # Mon..Sun
    start_time: time
    end_time: time
    event_type: str  # meeting/commute/on_site/lunch/other
    interruptible: bool = False
    title: str = ""


@dataclass
class TaskIn:
    id: int
    title: str
    cognitive_load: int
    estimated_duration_minutes: int
    deadline: Optional[datetime]
    priority: str  # Low/Medium/High
    time_window: str = "any"  # any | morning | afternoon
    not_before: Optional[datetime] = None


@dataclass
class Slot:
    day_of_week: str
    start: time
    end: time
    duration_minutes: int
    residue_penalty: float
    preceding_event_type: Optional[str] = None


@dataclass
class Assignment:
    task_id: int
    task_title: str
    cognitive_load: int
    day_of_week: Optional[str]
    slot_start: Optional[time]
    slot_end: Optional[time]
    residue_penalty_applied: float
    override_reason: Optional[str]
    reasoning_text: str
    unschedulable: bool = False
    split_label: Optional[str] = None
    factors: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def default_duration_for_load(load: int) -> int:
    return LOAD_DEFAULT_DURATION.get(load, 60)


def compute_urgency(task: TaskIn, reference_date: date) -> int:
    score = PRIORITY_WEIGHT.get(task.priority, 2)
    if task.deadline is not None:
        days_out = (task.deadline.date() - reference_date).days
        if days_out <= 2:
            score += 2
        elif days_out <= 5:
            score += 1
    return score


# ---------------------------------------------------------------------------
# Residue
# ---------------------------------------------------------------------------


def _day_residue(day_events: list[EventIn], prefs: Preferences) -> list[tuple[EventIn, float]]:
    """r0 (starting residue) for every event on a day, in (start, end) order."""
    ordered = sorted(day_events, key=lambda e: (_minutes(e.start_time), _minutes(e.end_time)))
    chain = 0
    prev_meeting_end: Optional[time] = None
    result: list[tuple[EventIn, float]] = []
    for ev in ordered:
        if ev.event_type == "meeting":
            stacked = prev_meeting_end is not None and (
                _minutes(ev.start_time) - _minutes(prev_meeting_end)
            ) <= prefs.stack_gap_minutes
            chain = min(chain + 1, MAX_STACKED_BONUS_COUNT) if stacked else 1
            prev_meeting_end = ev.end_time
        else:
            chain, prev_meeting_end = 0, None
        bonus = CONSECUTIVE_MEETING_BONUS * (chain - 1) if ev.event_type == "meeting" else 0.0
        r0 = _clamp(BASE_PENALTY_BY_TYPE.get(ev.event_type, 0.05) + bonus, PENALTY_MIN, PENALTY_MAX)
        result.append((ev, r0))
    return result


def _residue_at(x_min: int, day_residue: list[tuple[EventIn, float]], half_life: int):
    """Returns (R(x), event) -- the largest decayed residue at minute x and
    the event responsible for it, or (0.0, None) if none has ended yet."""
    best = 0.0
    best_ev = None
    for ev, r0 in day_residue:
        end_m = _minutes(ev.end_time)
        if end_m <= x_min:
            decayed = r0 * (2 ** (-(x_min - end_m) / half_life))
            if decayed > best:
                best = decayed
                best_ev = ev
    return best, best_ev


# ---------------------------------------------------------------------------
# Day state
# ---------------------------------------------------------------------------


@dataclass
class _DayState:
    day: str
    date: date
    available: bool
    open_min: int
    close_min: int
    events: list[EventIn]
    residue: list[tuple[EventIn, float]]
    placed_blocks: list[tuple[int, int, bool, str]] = field(default_factory=list)
    deep_minutes: int = 0


def _build_day_states(
    events: list[EventIn], week_start: date, prefs: Preferences, now_q: datetime
) -> dict[str, _DayState]:
    states: dict[str, _DayState] = {}
    for day in prefs.workdays:
        d = week_start + timedelta(days=WEEK.index(day))
        day_events = [e for e in events if e.day_of_week == day]
        if d < now_q.date():
            available, open_min, close_min = False, 0, 0
        else:
            open_min = (
                max(_minutes(prefs.workday_start), _minutes(now_q.time()))
                if d == now_q.date()
                else _minutes(prefs.workday_start)
            )
            close_min = _minutes(prefs.workday_end)
            available = open_min < close_min
        states[day] = _DayState(
            day=day,
            date=d,
            available=available,
            open_min=open_min,
            close_min=close_min,
            events=day_events,
            residue=_day_residue(day_events, prefs),
        )
    return states


def extract_free_slots(events: list[EventIn], prefs: Preferences | None = None) -> list[Slot]:
    """Raw gaps between events per day -- no buffer, no `now` clipping.
    Kept for callers/tests that just want the calendar's free gaps."""
    prefs = prefs or Preferences()
    slots: list[Slot] = []
    for day in prefs.workdays:
        day_events = sorted(
            (e for e in events if e.day_of_week == day), key=lambda e: _minutes(e.start_time)
        )
        residue = _day_residue([e for e in events if e.day_of_week == day], prefs)
        cursor = _minutes(prefs.workday_start)
        day_end = _minutes(prefs.workday_end)
        preceding_type: Optional[str] = None
        for ev in day_events:
            ev_start = _minutes(ev.start_time)
            ev_end = _minutes(ev.end_time)
            if ev_start > cursor:
                r, _ = _residue_at(cursor, residue, prefs.residue_half_life_minutes)
                slots.append(
                    Slot(
                        day_of_week=day,
                        start=_time_from_minutes(cursor),
                        end=_time_from_minutes(ev_start),
                        duration_minutes=ev_start - cursor,
                        residue_penalty=r,
                        preceding_event_type=preceding_type,
                    )
                )
            preceding_type = ev.event_type
            cursor = max(cursor, ev_end)
        if cursor < day_end:
            r, _ = _residue_at(cursor, residue, prefs.residue_half_life_minutes)
            slots.append(
                Slot(
                    day_of_week=day,
                    start=_time_from_minutes(cursor),
                    end=_time_from_minutes(day_end),
                    duration_minutes=day_end - cursor,
                    residue_penalty=r,
                    preceding_event_type=preceding_type,
                )
            )
    return slots


def _free_windows(ds: _DayState, ignore_interruptible: bool) -> list[tuple[int, int]]:
    if not ds.available:
        return []
    busy = []
    for e in ds.events:
        if ignore_interruptible and e.interruptible:
            continue
        busy.append((_minutes(e.start_time), _minutes(e.end_time)))
    for s, e, _is_deep, _title in ds.placed_blocks:
        busy.append((s, e))
    busy.sort()
    merged: list[list[int]] = []
    for s, e in busy:
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    windows: list[tuple[int, int]] = []
    cursor = ds.open_min
    for s, e in merged:
        if e <= cursor:
            continue
        if s > cursor:
            windows.append((cursor, min(s, ds.close_min)))
        cursor = max(cursor, e)
        if cursor >= ds.close_min:
            break
    if cursor < ds.close_min:
        windows.append((cursor, ds.close_min))
    return [(s, e) for s, e in windows if e > s]


def _window_earliest(ws: int, ds: _DayState, transition_buffer: int) -> int:
    earliest = ws
    for e in ds.events:
        if e.event_type in TRANSITION_TYPES:
            em = _minutes(e.end_time)
            if em <= ws:
                earliest = max(earliest, em + transition_buffer)
    return earliest


def _candidate_starts(ws: int, we: int, duration: int, ds: _DayState, transition_buffer: int) -> list[int]:
    """Candidates are `earliest` itself, plus every 15-minute-of-day mark
    (anchored to the window start, which is always on such a boundary)
    that falls after it -- not `earliest + 15k`. A transition buffer of a
    few minutes shouldn't shift the whole grid off the :00/:15/:30/:45
    marks; it just adds one early, slightly-off-grid opportunity right at
    the window's true start."""
    earliest = _window_earliest(ws, ds, transition_buffer)
    starts: set[int] = set()
    if earliest + duration <= we:
        starts.add(earliest)
    t = ws
    while t <= we:
        if t > earliest and t + duration <= we:
            starts.add(t)
        t += STEP_MINUTES
    return sorted(starts)


def _lunch_bounds(ds: _DayState) -> Optional[tuple[int, int]]:
    lunches = sorted(
        (e for e in ds.events if e.event_type == "lunch"), key=lambda e: _minutes(e.start_time)
    )
    if not lunches:
        return None
    return _minutes(lunches[0].start_time), _minutes(lunches[0].end_time)


def _time_window_ok(task: TaskIn, start: int, end: int, ds: _DayState) -> bool:
    if task.time_window == "morning":
        lunch = _lunch_bounds(ds)
        limit = lunch[0] if lunch else 12 * 60
        return end <= limit
    if task.time_window == "afternoon":
        lunch = _lunch_bounds(ds)
        limit = lunch[1] if lunch else 13 * 60
        return start >= limit
    return True


def _deep_cap_ok(ds: _DayState, prefs: Preferences, duration: int) -> bool:
    return ds.deep_minutes + duration <= prefs.deep_cap(ds.day)


def _deep_break_ok(ds: _DayState, prefs: Preferences, start: int, end: int) -> Optional[str]:
    """None if OK, else the title of the conflicting deep block."""
    gap = prefs.deep_break_minutes
    for bs, be, is_deep, title in ds.placed_blocks:
        if not is_deep:
            continue
        if not (start >= be + gap or end <= bs - gap):
            return title
    return None


def _overlapping_interruptible(ds: _DayState, start: int, end: int) -> Optional[EventIn]:
    for e in ds.events:
        if e.interruptible and _minutes(e.start_time) < end and start < _minutes(e.end_time):
            return e
    return None


def _valid_candidate(
    task: TaskIn,
    ds: _DayState,
    start: int,
    duration: int,
    prefs: Preferences,
    relax_deep_cap: bool,
    relax_deep_break: bool,
) -> bool:
    end = start + duration
    if task.deadline is not None:
        if datetime.combine(ds.date, _time_from_minutes(end)) > task.deadline:
            return False
    if not _time_window_ok(task, start, end, ds):
        return False
    if task.not_before is not None:
        if datetime.combine(ds.date, _time_from_minutes(start)) < task.not_before:
            return False
    if prefs.is_deep(task.cognitive_load):
        if not relax_deep_cap and not _deep_cap_ok(ds, prefs, duration):
            return False
        if not relax_deep_break and _deep_break_ok(ds, prefs, start, end) is not None:
            return False
    return True


# ---------------------------------------------------------------------------
# Cost / focus
# ---------------------------------------------------------------------------


def _focus_and_energy_at(x_min: int, prefs: Preferences, day: str, residue) -> tuple[float, float]:
    energy = min(1.0, prefs.energy_at(x_min // 60) * prefs.day_scale(day))
    r, _ = _residue_at(x_min, residue, prefs.residue_half_life_minutes)
    return energy * (1 - r), energy


def _cost_and_means(start: int, duration: int, load: int, prefs: Preferences, day: str, residue):
    n = -(-duration // STEP_MINUTES)  # ceil
    focus_total = 0.0
    energy_total = 0.0
    for k in range(n):
        f, e = _focus_and_energy_at(start + STEP_MINUTES * k, prefs, day, residue)
        focus_total += f
        energy_total += e
    mean_focus = focus_total / n
    mean_energy = energy_total / n
    cost = round((load / 13) * (1 - mean_focus), 4)
    if cost < MIN_SIGNIFICANT_COST:
        cost = 0.0
    return cost, mean_focus, mean_energy


# ---------------------------------------------------------------------------
# Explanation text
# ---------------------------------------------------------------------------


def render_reason(
    task: TaskIn, day: str, start_min: int, end_min: int, factors: dict, split_label: Optional[str], override: bool
) -> str:
    parts = []
    if split_label:
        i, n = split_label.split("/")
        parts.append(f"Part {i} of {n}")
    else:
        parts.append(
            f"{day} {_time_from_minutes(start_min).strftime('%H:%M')}"
            f"–{_time_from_minutes(end_min).strftime('%H:%M')}"
        )

    mean_energy = factors["mean_energy"]
    if mean_energy >= 0.95:
        energy_label = "peak energy"
    elif mean_energy >= 0.8:
        energy_label = "good energy"
    else:
        energy_label = "low-energy hours"

    residue = factors["residue_at_start"]
    preceding = factors["preceding_event"]
    if residue < 0.02:
        residue_label = "no recent events"
    else:
        title = (preceding.get("title") or preceding.get("type")) if preceding else ""
        residue_label = f"{'light residue' if residue < 0.10 else 'residue'} from {title}"

    parts.append(f"{energy_label}, {residue_label} (focus {factors['mean_focus']:.2f})")

    if factors["deep_minutes_day"] is not None:
        hrs_done = factors["deep_minutes_day"] / 60
        hrs_cap = factors["deep_cap_day"] / 60
        parts.append(f"{day} deep work {hrs_done:g} h of {hrs_cap:g} h")

    if factors["deadline"]:
        ddt = datetime.strptime(factors["deadline"], "%Y-%m-%dT%H:%M")
        parts.append(f"due {ddt.strftime('%a %H:%M')}")

    if factors["urgent"] and override:
        parts.append("deadline forced this slot")

    return " · ".join(parts)


def _fmt_deadline(dt: datetime) -> str:
    return dt.strftime("%a %H:%M")


def _relax_messages(
    task: TaskIn, day: str, ds: _DayState, start: int, end: int, prefs: Preferences, relaxed: list[str]
) -> list[str]:
    messages = []
    for code in relaxed:
        if code == "deep_cap":
            after = ds.deep_minutes + task.estimated_duration_minutes
            messages.append(
                f"{day} deep work {after} min, over the {prefs.deep_cap(day)}-min cap, "
                f"to meet the {_fmt_deadline(task.deadline)} deadline"
            )
        elif code == "deep_break":
            other = _deep_break_ok(ds, prefs, start, end)
            messages.append(
                f"Less than {prefs.deep_break_minutes} min from {other or 'other deep work'} "
                f"to meet the {_fmt_deadline(task.deadline)} deadline"
            )
        elif code == "overlaps_interruptible":
            ev = _overlapping_interruptible(ds, start, end)
            title = ev.title if ev and ev.title else "an interruptible event"
            messages.append(
                f"Overlaps {title} (interruptible); move or skip it to meet the "
                f"{_fmt_deadline(task.deadline)} deadline"
            )
    return messages


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def _best_candidate(
    task: TaskIn,
    day_states: dict[str, _DayState],
    prefs: Preferences,
    ignore_interruptible: bool,
    relax_deep_cap: bool,
    relax_deep_break: bool,
):
    best = None
    for day_idx, day in enumerate(prefs.workdays):
        ds = day_states[day]
        for ws, we in _free_windows(ds, ignore_interruptible):
            for start in _candidate_starts(ws, we, task.estimated_duration_minutes, ds, prefs.transition_buffer_minutes):
                if not _valid_candidate(task, ds, start, task.estimated_duration_minutes, prefs, relax_deep_cap, relax_deep_break):
                    continue
                cost, mean_focus, mean_energy = _cost_and_means(
                    start, task.estimated_duration_minutes, task.cognitive_load, prefs, day, ds.residue
                )
                key = (cost, day_idx, start)
                if best is None or key < best[0]:
                    best = (key, day, ds, start, mean_focus, mean_energy)
    return best


def _commit_block(ds: _DayState, start: int, end: int, is_deep: bool, title: str) -> None:
    ds.placed_blocks.append((start, end, is_deep, title))
    if is_deep:
        ds.deep_minutes += end - start


def _build_factors(
    task: TaskIn,
    day: str,
    ds: _DayState,
    start: int,
    end: int,
    prefs: Preferences,
    cost: float,
    mean_focus: float,
    mean_energy: float,
    urgent: bool,
    relaxed: list[str],
) -> dict:
    residue, preceding_ev = _residue_at(start, ds.residue, prefs.residue_half_life_minutes)
    is_deep = prefs.is_deep(task.cognitive_load)
    return {
        "mode": "focus",
        "cost": cost,
        "mean_focus": round(mean_focus, 2),
        "mean_energy": round(mean_energy, 2),
        "residue_at_start": round(residue, 2),
        "preceding_event": (
            {
                "title": preceding_ev.title,
                "type": preceding_ev.event_type,
                "end": preceding_ev.end_time.strftime("%H:%M"),
            }
            if preceding_ev
            else None
        ),
        "deep_minutes_day": (ds.deep_minutes + (end - start)) if is_deep else None,
        "deep_cap_day": prefs.deep_cap(day) if is_deep else None,
        "urgent": urgent,
        "deadline": task.deadline.strftime("%Y-%m-%dT%H:%M") if task.deadline else None,
        "relaxed": relaxed,
    }


def _place_single(
    task: TaskIn, day_states: dict[str, _DayState], prefs: Preferences, urgent: bool
) -> Optional[Assignment]:
    passes = [(False, False, False, [])]
    if urgent:
        passes = [
            (False, False, False, []),
            (False, True, False, ["deep_cap"]),
            (False, True, True, ["deep_cap", "deep_break"]),
            (True, True, True, ["deep_cap", "deep_break"]),
        ]

    result = None
    for ignore_interruptible, relax_cap, relax_break, _codes in passes:
        result = _best_candidate(task, day_states, prefs, ignore_interruptible, relax_cap, relax_break)
        if result is not None:
            break
    if result is None:
        return None

    (_key, day, ds, start, mean_focus, mean_energy) = result
    duration = task.estimated_duration_minutes
    end = start + duration
    cost = _key[0]

    relaxed = []
    is_deep = prefs.is_deep(task.cognitive_load)
    if is_deep and not _deep_cap_ok(ds, prefs, duration):
        relaxed.append("deep_cap")
    if is_deep and _deep_break_ok(ds, prefs, start, end) is not None:
        relaxed.append("deep_break")
    if _overlapping_interruptible(ds, start, end) is not None:
        relaxed.append("overlaps_interruptible")

    override = urgent and (cost > 0 or bool(relaxed))
    factors = _build_factors(task, day, ds, start, end, prefs, cost, mean_focus, mean_energy, urgent, relaxed)
    reasoning = render_reason(task, day, start, end, factors, None, override)
    warnings = _relax_messages(task, day, ds, start, end, prefs, relaxed) if urgent else []

    _commit_block(ds, start, end, is_deep, task.title)

    return Assignment(
        task_id=task.id,
        task_title=task.title,
        cognitive_load=task.cognitive_load,
        day_of_week=day,
        slot_start=_time_from_minutes(start),
        slot_end=_time_from_minutes(end),
        residue_penalty_applied=factors["residue_at_start"],
        override_reason=reasoning if override else None,
        reasoning_text=reasoning,
        factors=factors,
        warnings=warnings,
    )


def _split_walk(
    task: TaskIn,
    day_states: dict[str, _DayState],
    prefs: Preferences,
    relax_cap: bool,
    relax_break: bool,
):
    snapshot = {day: (ds.deep_minutes, list(ds.placed_blocks)) for day, ds in day_states.items()}
    remaining = task.estimated_duration_minutes
    is_deep = prefs.is_deep(task.cognitive_load)
    min_session = prefs.min_session_deep_minutes if is_deep else prefs.min_session_light_minutes
    sessions: list[tuple[str, int, int]] = []

    for day in prefs.workdays:
        if remaining <= 0:
            break
        ds = day_states[day]
        for ws, we in _free_windows(ds, ignore_interruptible=False):
            if remaining <= 0:
                break
            start = _window_earliest(ws, ds, prefs.transition_buffer_minutes)
            if start >= we:
                continue
            usable = we - start

            if task.not_before is not None and datetime.combine(ds.date, _time_from_minutes(start)) < task.not_before:
                continue
            if task.deadline is not None:
                if ds.date > task.deadline.date():
                    continue
                if ds.date == task.deadline.date():
                    deadline_min = _minutes(task.deadline.time())
                    if start >= deadline_min:
                        continue
                    usable = min(usable, deadline_min - start)
            if task.time_window == "morning":
                lunch = _lunch_bounds(ds)
                limit = lunch[0] if lunch else 12 * 60
                if start >= limit:
                    continue
                usable = min(usable, limit - start)
            elif task.time_window == "afternoon":
                lunch = _lunch_bounds(ds)
                limit = lunch[1] if lunch else 13 * 60
                if start < limit:
                    continue

            if usable <= 0:
                continue
            take = min(usable, remaining)

            if is_deep:
                if not relax_break and _deep_break_ok(ds, prefs, start, start + take) is not None:
                    continue
                if not relax_cap:
                    room = prefs.deep_cap(day) - ds.deep_minutes
                    if room <= 0:
                        continue
                    take = min(take, room)

            if take < min(min_session, remaining):
                continue

            leftover = remaining - take
            if 0 < leftover < min_session:
                shortened = remaining - min_session
                if shortened < min_session:
                    continue
                take = shortened

            end = start + take
            sessions.append((day, start, end))
            _commit_block(ds, start, end, is_deep, task.title)
            remaining -= take

    if remaining > 0:
        for day, (dm, blocks) in snapshot.items():
            day_states[day].deep_minutes = dm
            day_states[day].placed_blocks = blocks
        return None
    return sessions


def _place_split(
    task: TaskIn, day_states: dict[str, _DayState], prefs: Preferences, urgent: bool
) -> Optional[list[Assignment]]:
    passes = [(False, False)]
    if urgent:
        passes = [(False, False), (True, False), (True, True)]

    sessions = None
    for relax_cap, relax_break in passes:
        sessions = _split_walk(task, day_states, prefs, relax_cap, relax_break)
        if sessions is not None:
            break
    if sessions is None:
        return None

    total = len(sessions)
    assignments = []
    for i, (day, start, end) in enumerate(sessions, start=1):
        ds = day_states[day]
        duration = end - start
        cost, mean_focus, mean_energy = _cost_and_means(start, duration, task.cognitive_load, prefs, day, ds.residue)
        is_deep = prefs.is_deep(task.cognitive_load)
        relaxed = []
        if is_deep and ds.deep_minutes - duration + duration > prefs.deep_cap(day):
            # deep minutes already includes this block (committed during walk)
            if ds.deep_minutes > prefs.deep_cap(day):
                relaxed.append("deep_cap")
        split_label = f"{i}/{total}" if total > 1 else None
        override = urgent
        factors = _build_factors(task, day, ds, start, end, prefs, cost, mean_focus, mean_energy, urgent, relaxed)
        # deep_minutes_day in factors should reflect state *after* this block, which ds.deep_minutes already is.
        if is_deep:
            factors["deep_minutes_day"] = ds.deep_minutes
        reasoning = render_reason(task, day, start, end, factors, split_label, override)
        assignments.append(
            Assignment(
                task_id=task.id,
                task_title=task.title,
                cognitive_load=task.cognitive_load,
                day_of_week=day,
                slot_start=_time_from_minutes(start),
                slot_end=_time_from_minutes(end),
                residue_penalty_applied=factors["residue_at_start"],
                override_reason=reasoning if (override and total > 0) else None,
                reasoning_text=reasoning,
                split_label=split_label,
                factors=factors,
                warnings=[],
            )
        )
    return assignments


def _unschedulable(task: TaskIn, day_states: dict[str, _DayState], prefs: Preferences) -> Assignment:
    reasoning = _diagnose_unschedulable(task, day_states, prefs)
    return Assignment(
        task_id=task.id,
        task_title=task.title,
        cognitive_load=task.cognitive_load,
        day_of_week=None,
        slot_start=None,
        slot_end=None,
        residue_penalty_applied=0.0,
        override_reason=None,
        reasoning_text=reasoning,
        unschedulable=True,
    )


def _diagnose_unschedulable(task: TaskIn, day_states: dict[str, _DayState], prefs: Preferences) -> str:
    duration = task.estimated_duration_minutes
    is_deep = prefs.is_deep(task.cognitive_load)
    tail = " Consider moving an event or extending the deadline." if task.deadline else ""

    # Stage 1: raw free-window fit
    best = 0
    ok = False
    for day in prefs.workdays:
        ds = day_states[day]
        for ws, we in _free_windows(ds, False):
            start = _window_earliest(ws, ds, prefs.transition_buffer_minutes)
            usable = max(0, we - start)
            best = max(best, usable)
            if usable >= duration:
                ok = True
    if not ok:
        return f"Needs {duration} min; the longest free window this week is {best} min."

    # Stage 2: + deadline
    if task.deadline is not None:
        open_days = [
            datetime.combine(ds.date, _time_from_minutes(ds.open_min))
            for ds in day_states.values()
            if ds.available
        ]
        if open_days and min(open_days) >= task.deadline:
            return (
                f"Deadline {task.deadline.strftime('%a %b %d %H:%M')} has already passed; "
                f"move it later to schedule this task."
            )
        best2 = 0
        ok2 = False
        for day in prefs.workdays:
            ds = day_states[day]
            if ds.date > task.deadline.date():
                continue
            for ws, we in _free_windows(ds, False):
                start = _window_earliest(ws, ds, prefs.transition_buffer_minutes)
                usable = we - start
                if ds.date == task.deadline.date():
                    usable = min(usable, _minutes(task.deadline.time()) - start)
                usable = max(0, usable)
                best2 = max(best2, usable)
                if usable >= duration:
                    ok2 = True
        if not ok2:
            return (
                f"Needs {duration} min before {_fmt_deadline(task.deadline)}; the longest "
                f"free window before then is {best2} min.{tail}"
            )

    def _window_candidate_ok(ds, ws, we):
        start = _window_earliest(ws, ds, prefs.transition_buffer_minutes)
        end = start + duration
        if end > we:
            return False
        if task.deadline is not None and ds.date == task.deadline.date():
            if datetime.combine(ds.date, _time_from_minutes(end)) > task.deadline:
                return False
        if task.not_before is not None and datetime.combine(ds.date, _time_from_minutes(start)) < task.not_before:
            return False
        if not _time_window_ok(task, start, end, ds):
            return False
        return True

    # Stage 3: + time window / not-before
    ok3 = any(
        _window_candidate_ok(day_states[day], ws, we)
        for day in prefs.workdays
        if not (task.deadline and day_states[day].date > task.deadline.date())
        for ws, we in _free_windows(day_states[day], False)
    )
    if not ok3:
        if task.time_window != "any":
            msg = f"No {duration}-min {task.time_window} window"
            msg += f" before {_fmt_deadline(task.deadline)}." if task.deadline else "."
        else:
            msg = f"No {duration}-min window after {_fmt_deadline(task.not_before)}."
        return msg + tail

    if not is_deep:
        return f"Needs {duration} min; the longest free window this week is {best} min.{tail}"

    # Stage 4: + deep cap
    def _cap_ok(ds, ws, we):
        if ds.deep_minutes + duration > prefs.deep_cap(ds.day):
            return False
        return _window_candidate_ok(ds, ws, we)

    ok4 = any(
        _cap_ok(day_states[day], ws, we)
        for day in prefs.workdays
        if not (task.deadline and day_states[day].date > task.deadline.date())
        for ws, we in _free_windows(day_states[day], False)
    )
    if not ok4:
        return f"Needs {duration} min of deep work, but every day with room is at its {prefs.max_deep_minutes_per_day}-min cap.{tail}"

    # Stage 5: + deep break
    def _break_ok(ds, ws, we):
        if not _cap_ok(ds, ws, we):
            return False
        start = _window_earliest(ws, ds, prefs.transition_buffer_minutes)
        end = start + duration
        return _deep_break_ok(ds, prefs, start, end) is None

    ok5 = any(
        _break_ok(day_states[day], ws, we)
        for day in prefs.workdays
        if not (task.deadline and day_states[day].date > task.deadline.date())
        for ws, we in _free_windows(day_states[day], False)
    )
    if not ok5:
        return f"Only fits right next to other deep work; needs a {prefs.deep_break_minutes}-min break.{tail}"

    return f"Needs {duration} min; the longest free window this week is {best} min.{tail}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_plan(
    tasks: list[TaskIn],
    events: list[EventIn],
    week_start: date,
    *,
    prefs: Preferences | None = None,
    now: datetime | None = None,
    allow_split: bool = False,
) -> list[Assignment]:
    prefs = prefs or Preferences()
    now = now if now is not None else datetime.combine(week_start, time.min)
    now_q = _round_up_15(now)
    today = now.date()

    day_states = _build_day_states(events, week_start, prefs, now_q)

    scored = [(t, compute_urgency(t, today)) for t in tasks]
    urgent = sorted(
        (t for t, s in scored if s >= URGENT_THRESHOLD),
        key=lambda t: (t.deadline or datetime.max, -compute_urgency(t, today), t.id),
    )
    others = sorted(
        (t for t, s in scored if s < URGENT_THRESHOLD),
        key=lambda t: (-t.cognitive_load, -t.estimated_duration_minutes, t.id),
    )

    assignments: list[Assignment] = []

    for task in urgent:
        if allow_split:
            sessions = _place_split(task, day_states, prefs, urgent=True)
            assignments.extend(sessions if sessions is not None else [_unschedulable(task, day_states, prefs)])
        else:
            a = _place_single(task, day_states, prefs, urgent=True)
            assignments.append(a if a is not None else _unschedulable(task, day_states, prefs))

    for task in others:
        if allow_split:
            sessions = _place_split(task, day_states, prefs, urgent=False)
            assignments.extend(sessions if sessions is not None else [_unschedulable(task, day_states, prefs)])
        else:
            a = _place_single(task, day_states, prefs, urgent=False)
            assignments.append(a if a is not None else _unschedulable(task, day_states, prefs))

    return assignments
