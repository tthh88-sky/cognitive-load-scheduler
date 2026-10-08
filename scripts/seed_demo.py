"""Seeds scheduler.db with a realistic demo persona: a backend engineer on a
platform team with a meeting-heavy Tuesday, a focus-day Wednesday, a backlog
of open tasks due in the target week, and ~3 weeks of completed-task history
(so the Insights panel and the estimator's "adjusted +N% from your history"
are live from the first frame).

    python scripts/seed_demo.py               # target next week (default)
    python scripts/seed_demo.py --week this   # target the current week

Wipes events, tasks and plans first, after copying the db file to
scheduler.db.bak-<timestamp>. Honours SCHEDULER_DB_URL like the app does.
Deadlines and one-off events are dated relative to the target week, so re-run
it if you record in a later week.
"""

import argparse
import os
import shutil
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app import models  # noqa: E402
from app.calibration import hindsight_load  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.migrations import migrate  # noqa: E402
from app.models import DayOfWeek, EventType, Priority, TaskStatus  # noqa: E402
from app.preferences import Preferences  # noqa: E402

MODEL = "qwen3.5:9b"

ABOUT_ME = (
    "Backend engineer on the platform team (Python, Postgres). I'm sharpest "
    "before lunch; after 3pm I'm best at reviews and admin. I know the core "
    "API service inside out but I'm still new to the data pipeline. I tend to "
    "underestimate anything involving schema changes or coordinating with "
    "other teams."
)

PREFS = Preferences(workday_start=time(9, 0), workday_end=time(18, 0))

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# (day, start, end, title, type, interruptible) -- recurring weekly
WEEKLY_EVENTS = [
    *[(d, "09:30", "09:45", "Daily standup", EventType.MEETING, False) for d in DAYS],
    *[(d, "12:30", "13:30", "Lunch", EventType.LUNCH, False) for d in DAYS],
    ("Mon", "14:00", "15:00", "Sprint planning", EventType.MEETING, False),
    ("Tue", "10:00", "11:00", "Design review: data model v2", EventType.MEETING, False),
    ("Tue", "11:15", "12:00", "1:1 with manager", EventType.MEETING, False),
    ("Tue", "14:00", "15:00", "Cross-team sync (Product + Data)", EventType.MEETING, False),
    ("Tue", "16:00", "17:00", "Architecture forum", EventType.MEETING, True),
    ("Wed", "16:30", "17:00", "Team demo", EventType.MEETING, False),
    ("Thu", "10:00", "11:00", "Backend guild", EventType.MEETING, True),
    ("Thu", "15:00", "16:00", "Incident review", EventType.MEETING, False),
    ("Fri", "16:00", "17:00", "Sprint retro", EventType.MEETING, False),
]

# (day offset from Monday, start, end, title, type) -- one-off, this week only
ONE_OFF_EVENTS = [
    (3, "13:30", "14:30", "Vendor demo: observability tooling", EventType.MEETING),
    (4, "09:45", "11:15", "Office visit: infrastructure team", EventType.ON_SITE),
]

# Open backlog. deadline = (day offset from Monday, "HH:MM") or None.
# ai = (load, duration, confidence, rationale) when the AI suggested it; the
# task's own load/duration differing from ai means the user edited it.
OPEN_TASKS = [
    dict(
        title="Investigate p95 latency spike on /search",
        description="p95 doubled since Thursday's deploy; only on queries with filters.",
        load=5, minutes=120, priority=Priority.HIGH, deadline=(1, "17:00"),
        ai=(5, 120, "medium", "Open-ended investigation across deploy diff, metrics and DB plans, but in a service you know well."),
    ),
    dict(
        title="Write migration plan for users table split",
        description="Split users into users + user_profiles with zero downtime; needs Data team sign-off.",
        load=8, minutes=150, priority=Priority.HIGH, deadline=(2, "18:00"),
        ai=(8, 150, "high", "Schema change with backfill and cutover planning plus cross-team coordination."),
    ),
    dict(
        title="Design idempotency keys for bulk import API",
        description="Duplicate records when clients retry on timeout. Propose key format, storage and TTL.",
        load=8, minutes=150, priority=Priority.HIGH, deadline=(3, "17:00"),
        ai=(8, 120, "high", "New API contract with storage and concurrency decisions that other teams will depend on."),
    ),
    dict(
        title="Fix flaky login integration test",
        description="test_login_flow fails ~1 in 20 on CI, never locally.",
        load=3, minutes=60, priority=Priority.MEDIUM, deadline=(4, "15:00"),
        ai=(3, 60, "medium", "Needs investigation to find the race, but the scope is a single test."),
    ),
    dict(
        title="Review PR: date parsing helper",
        description="",
        load=2, minutes=30, priority=Priority.MEDIUM, deadline=(1, "18:00"),
        ai=None,
    ),
    dict(
        title="Reply to Data team on Q3 usage numbers",
        description="",
        load=1, minutes=15, priority=Priority.MEDIUM, deadline=(0, "17:00"),
        ai=None,
    ),
    dict(
        title="Update on-call runbook for job retries",
        description="Add the new retry backoff and the manual replay steps.",
        load=2, minutes=45, priority=Priority.LOW, deadline=(4, "18:00"),
        ai=(2, 30, "high", "Documentation of a process you already know; little new reasoning required."),
    ),
    dict(
        title="Prototype background job failure dashboard",
        description="Grafana panel for failed jobs by queue and error type.",
        load=5, minutes=120, priority=Priority.LOW, deadline=None,
        ai=(5, 120, "medium", "New dashboard in an unfamiliar data source with some design choices."),
    ),
    dict(
        title="Bump core service to Python 3.12",
        description="",
        load=3, minutes=60, priority=Priority.LOW, deadline=None,
        ai=None,
    ),
]

# Completed history: (days ago, title, load, ai_load, ai_minutes, actual, hindsight)
# Deep work (load >= 5) consistently runs ~35% over the AI's estimate; light
# work lands close to it -- that's what the duration multiplier learns from.
HISTORY = [
    (20, "Schema migration for audit log entries", 8, 8, 150, 225, "harder"),
    (19, "Fix rounding bug in report totals", 3, 3, 60, 55, "right"),
    (18, "Review PR: webhook signature check", 2, 2, 30, 30, "right"),
    (17, "Design batch export API", 8, 8, 180, 240, "right"),
    (16, "Update README for local dev setup", 1, 1, 15, 15, "right"),
    (13, "Add retry with backoff to HTTP client", 5, 5, 120, 165, "right"),
    (12, "Investigate duplicate record reports", 5, 3, 60, 110, "harder"),
    (11, "Review PR: status enum cleanup", 2, 2, 30, 35, "right"),
    (10, "Answer Data team questions on August usage", 1, 1, 15, 20, "right"),
    (9, "Write data pipeline job for daily export", 8, 5, 120, 170, "harder"),
    (6, "Fix timezone bug in scheduled reminders", 3, 3, 45, 50, "right"),
    (5, "Spike: evaluate message queue options", 5, 5, 120, 150, "right"),
    (4, "Rotate API keys for staging environments", 1, 2, 30, 25, "easier"),
    (3, "Add index for slow lookup query", 3, 3, 60, 55, "right"),
    (2, "Design schema for soft-deleted records", 8, 8, 150, 210, "right"),
]


def _t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


def _backup(db_url_path: str | None) -> str | None:
    if not db_url_path or not os.path.exists(db_url_path) or os.path.getsize(db_url_path) == 0:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{db_url_path}.bak-{stamp}"
    shutil.copy2(db_url_path, dest)
    return dest


def seed(week: str) -> None:
    backup = _backup(engine.url.database)
    Base.metadata.create_all(bind=engine)
    migrate(engine)

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    monday = this_monday if week == "this" else this_monday + timedelta(days=7)
    now = datetime.now()

    db = SessionLocal()
    try:
        db.query(models.PlanAssignment).delete()
        db.query(models.Task).delete()
        db.query(models.Event).delete()

        settings = db.get(models.PlanSettings, 1) or models.PlanSettings(id=1)
        settings.allow_split = False
        settings.preferences_json = PREFS.model_dump_json()
        settings.about_me = ABOUT_ME
        db.merge(settings)

        for day, start, end, title, etype, interruptible in WEEKLY_EVENTS:
            db.add(models.Event(
                title=title, day_of_week=DayOfWeek(day), start_time=_t(start),
                end_time=_t(end), event_type=etype, is_interruptible=interruptible,
            ))
        for offset, start, end, title, etype in ONE_OFF_EVENTS:
            d = monday + timedelta(days=offset)
            db.add(models.Event(
                title=title, day_of_week=DayOfWeek(d.strftime("%a")), start_time=_t(start),
                end_time=_t(end), event_type=etype, is_interruptible=False, date=d,
            ))

        for i, spec in enumerate(OPEN_TASKS):
            deadline = None
            if spec["deadline"]:
                offset, hhmm = spec["deadline"]
                deadline = datetime.combine(monday + timedelta(days=offset), _t(hhmm))
            task = models.Task(
                title=spec["title"], description=spec["description"] or None,
                cognitive_load=spec["load"], estimated_duration_minutes=spec["minutes"],
                deadline=deadline, priority=spec["priority"], status=TaskStatus.TODO,
                created_at=now - timedelta(hours=len(OPEN_TASKS) - i),
                load_source="manual", time_window="any",
            )
            if spec["ai"]:
                ai_load, ai_minutes, confidence, rationale = spec["ai"]
                same = (ai_load, ai_minutes) == (spec["load"], spec["minutes"])
                task.load_source = "ai_accepted" if same else "ai_edited"
                task.ai_load, task.ai_duration_minutes = ai_load, ai_minutes
                task.ai_confidence, task.ai_rationale, task.ai_model = confidence, rationale, MODEL
            db.add(task)

        for days_ago, title, load, ai_load, ai_minutes, actual, hindsight in HISTORY:
            completed = datetime.combine(today - timedelta(days=days_ago), time(16, 0))
            db.add(models.Task(
                title=title, cognitive_load=load, estimated_duration_minutes=ai_minutes,
                priority=Priority.MEDIUM, status=TaskStatus.DONE,
                created_at=completed - timedelta(days=2),
                load_source="ai_accepted" if load == ai_load else "ai_edited",
                ai_load=ai_load, ai_duration_minutes=ai_minutes, ai_confidence="medium",
                ai_rationale="", ai_model=MODEL,
                started_at=completed - timedelta(minutes=actual),
                completed_at=completed, actual_minutes=actual,
                hindsight_load=hindsight_load(load, hindsight), time_window="any",
            ))

        db.commit()
    finally:
        db.close()

    if backup:
        print(f"Backed up previous db to {backup}")
    print(
        f"Seeded {len(WEEKLY_EVENTS) + len(ONE_OFF_EVENTS)} events, {len(OPEN_TASKS)} open tasks, "
        f"{len(HISTORY)} completed tasks for the week of {monday:%a %d %b %Y}."
    )
    print(f"Open /plan?week={week} and click Generate.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--week", choices=["this", "next"], default="next")
    seed(parser.parse_args().week)
