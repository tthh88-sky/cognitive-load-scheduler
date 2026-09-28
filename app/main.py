import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import calibration, grid, models, scheduler
from app.database import Base, engine, get_db
from app.llm import estimator
from app.llm import provider as llm_provider
from app.llm.provider import LLMUnavailable
from app.migrations import migrate
from app.models import DayOfWeek, EventType, Priority, TaskStatus
from app.preferences import WEEK, DayModifier, ENERGY_PRESETS, Preferences
from app.schemas import EventCreate

logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)
migrate(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    provider = app.dependency_overrides.get(llm_provider.get_provider_dependency, llm_provider.get_provider_dependency)()
    threading.Thread(target=provider.preload, daemon=True).start()
    yield


app = FastAPI(title="Cognitive-Load Aware Task Scheduler", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

DAY_ORDER = list(WEEK)  # Mon..Sun, engine order


LABEL_STEP_MINUTES = 30


def _day_span_minutes(open_t: time, close_t: time) -> int:
    open_m = open_t.hour * 60 + open_t.minute
    close_m = close_t.hour * 60 + close_t.minute
    return max(0, close_m - open_m)


def _hour_labels(open_t: time, close_t: time) -> list[tuple[int, str]]:
    open_m = open_t.hour * 60 + open_t.minute
    close_m = close_t.hour * 60 + close_t.minute
    labels = []
    step = LABEL_STEP_MINUTES
    m = ((open_m + step - 1) // step) * step
    while m < close_m:
        row_start = 2 + (m - open_m)
        labels.append((row_start, f"{m // 60:02d}:{m % 60:02d}"))
        m += step
    return labels


def current_week_start(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=today.weekday())


# ---------- Preferences storage ----------


def _get_plan_settings(db: Session) -> models.PlanSettings:
    settings = db.get(models.PlanSettings, 1)
    if settings is None:
        settings = models.PlanSettings(id=1, allow_split=False)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def load_prefs(db: Session) -> Preferences:
    settings = _get_plan_settings(db)
    if not settings.preferences_json:
        return Preferences()
    try:
        return Preferences.model_validate_json(settings.preferences_json)
    except (ValidationError, ValueError):
        logger.warning("Invalid preferences_json in plan_settings; using defaults")
        return Preferences()


def save_prefs(db: Session, prefs: Preferences) -> None:
    settings = _get_plan_settings(db)
    settings.preferences_json = prefs.model_dump_json()
    db.commit()


# ---------- Engine <-> ORM conversion ----------


def _task_in(t: models.Task) -> scheduler.TaskIn:
    return scheduler.TaskIn(
        id=t.id,
        title=t.title,
        cognitive_load=t.cognitive_load,
        estimated_duration_minutes=t.estimated_duration_minutes,
        deadline=t.deadline,
        priority=t.priority.value,
        time_window=t.time_window or "any",
        not_before=t.not_before,
    )


def _event_in(e: models.Event) -> scheduler.EventIn:
    day = e.date.strftime("%a") if e.date else e.day_of_week.value
    return scheduler.EventIn(
        day_of_week=day,
        start_time=e.start_time,
        end_time=e.end_time,
        event_type=e.event_type.value,
        interruptible=bool(e.is_interruptible),
        title=e.title or "",
    )


def _events_for_week(db: Session, week_start: date) -> list[models.Event]:
    weekly = db.query(models.Event).filter(models.Event.date.is_(None)).all()
    week_end = week_start + timedelta(days=6)
    dated = (
        db.query(models.Event)
        .filter(models.Event.date.isnot(None))
        .filter(models.Event.date >= week_start, models.Event.date <= week_end)
        .all()
    )
    return weekly + dated


# ---------- Page routes ----------


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/plan")


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, week: str = "this", db: Session = Depends(get_db)):
    prefs = load_prefs(db)
    this_week_start = current_week_start()
    week_start = this_week_start if week == "this" else this_week_start + timedelta(days=7)
    events = _events_for_week(db, week_start)

    items = []
    for ev in events:
        day = ev.date.strftime("%a") if ev.date else ev.day_of_week.value
        items.append((day, ev.start_time, ev.end_time, "event", ev))
    blocks = grid.build_week_blocks(prefs.workdays, prefs.workday_start, prefs.workday_end, items)

    lunch_event = (
        db.query(models.Event)
        .filter(models.Event.event_type == EventType.LUNCH)
        .first()
    )
    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request,
            "events": events,
            "blocks": blocks,
            "day_order": prefs.workdays,
            "hour_labels": _hour_labels(prefs.workday_start, prefs.workday_end),
            "day_span": _day_span_minutes(prefs.workday_start, prefs.workday_end),
            "week": week,
            "week_start": week_start,
            "event_types": [e.value for e in EventType if e != EventType.LUNCH],
            "days": WEEK,
            "lunch_start": (lunch_event.start_time if lunch_event else time(12, 0)).strftime("%H:%M"),
            "lunch_end": (lunch_event.end_time if lunch_event else time(13, 0)).strftime("%H:%M"),
            "lunch_set": lunch_event is not None,
        },
    )


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, db: Session = Depends(get_db)):
    tasks = db.query(models.Task).order_by(models.Task.created_at.desc()).all()
    outcomes = _build_outcomes(db)
    return templates.TemplateResponse(
        "tasks.html",
        {
            "request": request,
            "tasks": tasks,
            "load_anchors": scheduler.LOAD_ANCHOR,
            "load_defaults": scheduler.LOAD_DEFAULT_DURATION,
            "priorities": [p.value for p in Priority],
            "statuses": [s.value for s in TaskStatus],
            "time_windows": ["any", "morning", "afternoon"],
            "insights": calibration.insights(outcomes) if outcomes else None,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    prefs = load_prefs(db)
    settings = _get_plan_settings(db)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "prefs": prefs,
            "about_me": settings.about_me or "",
            "week": WEEK,
            "presets": ENERGY_PRESETS,
            "errors": [],
        },
    )


def _parse_settings_form(form) -> Preferences:
    workdays = form.getlist("workdays") or ["Mon", "Tue", "Wed", "Thu", "Fri"]

    energy_curve = {}
    for h in range(9, 19):
        v = form.get(f"energy_{h}")
        if v not in (None, ""):
            energy_curve[h] = float(v)
    if not energy_curve:
        energy_curve = dict(ENERGY_PRESETS["morning"])

    day_modifiers = {}
    for day in WEEK:
        scale = form.get(f"dm_scale_{day}")
        cap = form.get(f"dm_cap_{day}")
        if scale not in (None, "") or cap not in (None, ""):
            day_modifiers[day] = DayModifier(
                energy_scale=float(scale) if scale not in (None, "") else 1.0,
                max_deep_minutes=int(cap) if cap not in (None, "") else None,
            )

    return Preferences(
        workdays=workdays,
        workday_start=time.fromisoformat(form.get("workday_start", "09:00")),
        workday_end=time.fromisoformat(form.get("workday_end", "19:00")),
        energy_curve=energy_curve,
        residue_half_life_minutes=int(form.get("residue_half_life_minutes", 20)),
        transition_buffer_minutes=int(form.get("transition_buffer_minutes", 5)),
        stack_gap_minutes=int(form.get("stack_gap_minutes", 15)),
        deep_load_threshold=int(form.get("deep_load_threshold", 5)),
        max_deep_minutes_per_day=int(form.get("max_deep_minutes_per_day", 240)),
        deep_break_minutes=int(form.get("deep_break_minutes", 15)),
        min_session_deep_minutes=int(form.get("min_session_deep_minutes", 30)),
        min_session_light_minutes=int(form.get("min_session_light_minutes", 15)),
        day_modifiers=day_modifiers,
    )


@app.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        prefs = _parse_settings_form(form)
    except (ValidationError, ValueError) as exc:
        errors = [str(exc)]
        settings = _get_plan_settings(db)
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "prefs": load_prefs(db),
                "about_me": form.get("about_me", settings.about_me or ""),
                "week": WEEK,
                "presets": ENERGY_PRESETS,
                "errors": errors,
            },
            status_code=400,
        )

    save_prefs(db, prefs)
    settings = _get_plan_settings(db)
    settings.about_me = form.get("about_me", "")
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.get("/plan", response_class=HTMLResponse)
def plan_page(request: Request, week: str = "this", db: Session = Depends(get_db)):
    prefs = load_prefs(db)
    this_week_start = current_week_start()
    week_start = this_week_start if week == "this" else this_week_start + timedelta(days=7)
    assignments = (
        db.query(models.PlanAssignment)
        .filter(models.PlanAssignment.week_start_date == week_start)
        .all()
    )
    scheduled = [a for a in assignments if not a.unschedulable]
    unscheduled = [a for a in assignments if a.unschedulable]

    items = [(a.day_of_week.value, a.slot_start, a.slot_end, "task", a) for a in scheduled]
    blocks = grid.build_week_blocks(prefs.workdays, prefs.workday_start, prefs.workday_end, items)

    settings = _get_plan_settings(db)
    return templates.TemplateResponse(
        "plan.html",
        {
            "request": request,
            "week_start": week_start,
            "week": week,
            "blocks": blocks,
            "unscheduled": unscheduled,
            "day_order": prefs.workdays,
            "hour_labels": _hour_labels(prefs.workday_start, prefs.workday_end),
            "day_span": _day_span_minutes(prefs.workday_start, prefs.workday_end),
            "has_plan": len(assignments) > 0,
            "selected_allow_split": settings.allow_split,
        },
    )


@app.get("/overview", response_class=HTMLResponse)
def overview_page(request: Request, week: str = "this", db: Session = Depends(get_db)):
    prefs = load_prefs(db)
    this_week_start = current_week_start()
    week_start = this_week_start if week == "this" else this_week_start + timedelta(days=7)
    events = _events_for_week(db, week_start)
    assignments = (
        db.query(models.PlanAssignment)
        .filter(models.PlanAssignment.week_start_date == week_start)
        .filter(models.PlanAssignment.unschedulable == False)  # noqa: E712
        .all()
    )

    items = []
    for ev in events:
        day = ev.date.strftime("%a") if ev.date else ev.day_of_week.value
        items.append((day, ev.start_time, ev.end_time, "event", ev))
    for a in assignments:
        items.append((a.day_of_week.value, a.slot_start, a.slot_end, "task", a))
    blocks = grid.build_week_blocks(prefs.workdays, prefs.workday_start, prefs.workday_end, items)

    return templates.TemplateResponse(
        "overview.html",
        {
            "request": request,
            "week_start": week_start,
            "week": week,
            "blocks": blocks,
            "day_order": prefs.workdays,
            "hour_labels": _hour_labels(prefs.workday_start, prefs.workday_end),
            "day_span": _day_span_minutes(prefs.workday_start, prefs.workday_end),
        },
    )


# ---------- Event CRUD (HTMX partial responses) ----------


@app.post("/events", response_class=HTMLResponse)
def create_event(
    request: Request,
    title: str = Form(...),
    day_of_week: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    event_type: str = Form(...),
    is_interruptible: bool = Form(False),
    date_: str = Form("", alias="date"),
    db: Session = Depends(get_db),
):
    one_off_date = date.fromisoformat(date_) if date_ else None
    resolved_day = DAY_ORDER[one_off_date.weekday()] if one_off_date else day_of_week
    payload = EventCreate(
        title=title,
        day_of_week=resolved_day,
        start_time=start_time,
        end_time=end_time,
        event_type=event_type,
        is_interruptible=is_interruptible,
        date=one_off_date,
    )
    ev = models.Event(**payload.model_dump())
    db.add(ev)
    db.commit()
    return _calendar_partial(request, db)


@app.put("/events/{event_id}", response_class=HTMLResponse)
def update_event(
    request: Request,
    event_id: int,
    title: str = Form(...),
    day_of_week: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    event_type: str = Form(...),
    is_interruptible: bool = Form(False),
    date_: str = Form("", alias="date"),
    db: Session = Depends(get_db),
):
    ev = db.get(models.Event, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    one_off_date = date.fromisoformat(date_) if date_ else None
    resolved_day = DAY_ORDER[one_off_date.weekday()] if one_off_date else day_of_week
    payload = EventCreate(
        title=title,
        day_of_week=resolved_day,
        start_time=start_time,
        end_time=end_time,
        event_type=event_type,
        is_interruptible=is_interruptible,
        date=one_off_date,
    )
    for field, value in payload.model_dump().items():
        setattr(ev, field, value)
    db.commit()
    return _calendar_partial(request, db)


@app.delete("/events/{event_id}", response_class=HTMLResponse)
def delete_event(request: Request, event_id: int, db: Session = Depends(get_db)):
    ev = db.get(models.Event, event_id)
    if ev:
        db.delete(ev)
        db.commit()
    return _calendar_partial(request, db)


@app.post("/events/lunch", response_class=HTMLResponse)
def set_lunch_time(
    request: Request,
    start_time: str = Form(...),
    end_time: str = Form(...),
    db: Session = Depends(get_db),
):
    prefs = load_prefs(db)
    start = time.fromisoformat(start_time)
    end = time.fromisoformat(end_time)
    db.query(models.Event).filter(models.Event.event_type == EventType.LUNCH).delete()
    for day in prefs.workdays:
        db.add(
            models.Event(
                title="Lunch",
                day_of_week=DayOfWeek(day),
                start_time=start,
                end_time=end,
                event_type=EventType.LUNCH,
                is_interruptible=False,
            )
        )
    db.commit()
    return _calendar_partial(request, db)


def _calendar_partial(request: Request, db: Session):
    prefs = load_prefs(db)
    week_start = current_week_start()
    events = _events_for_week(db, week_start)
    items = []
    for ev in events:
        day = ev.date.strftime("%a") if ev.date else ev.day_of_week.value
        items.append((day, ev.start_time, ev.end_time, "event", ev))
    blocks = grid.build_week_blocks(prefs.workdays, prefs.workday_start, prefs.workday_end, items)
    return templates.TemplateResponse(
        "_calendar_grid.html",
        {
            "request": request,
            "blocks": blocks,
            "day_order": prefs.workdays,
            "hour_labels": _hour_labels(prefs.workday_start, prefs.workday_end),
            "day_span": _day_span_minutes(prefs.workday_start, prefs.workday_end),
            "week": "this",
        },
    )


# ---------- Task CRUD ----------


def _resolve_load_source_on_create(
    cognitive_load: int, duration: int, ai_load: int | None, ai_suggested_duration: int | None
) -> str:
    if ai_load is None:
        return "manual"
    if cognitive_load == ai_load and duration == ai_suggested_duration:
        return "ai_accepted"
    return "ai_edited"


@app.post("/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    cognitive_load: int = Form(...),
    estimated_duration_minutes: int = Form(None),
    deadline: str = Form(""),
    priority: str = Form(...),
    time_window: str = Form("any"),
    not_before: str = Form(""),
    ai_load: str = Form(""),
    ai_duration_minutes: str = Form(""),
    ai_suggested_duration: str = Form(""),
    ai_confidence: str = Form(""),
    ai_rationale: str = Form(""),
    ai_model: str = Form(""),
    db: Session = Depends(get_db),
):
    duration = estimated_duration_minutes or scheduler.default_duration_for_load(cognitive_load)
    deadline_dt = datetime.fromisoformat(deadline) if deadline else None
    not_before_dt = datetime.fromisoformat(not_before) if not_before else None
    ai_load = int(ai_load) if ai_load else None
    ai_duration_minutes = int(ai_duration_minutes) if ai_duration_minutes else None
    ai_suggested_duration = int(ai_suggested_duration) if ai_suggested_duration else None
    ai_confidence = ai_confidence or None
    ai_rationale = ai_rationale or None
    ai_model = ai_model or None
    load_source = _resolve_load_source_on_create(cognitive_load, duration, ai_load, ai_suggested_duration)
    task = models.Task(
        title=title,
        description=description or None,
        cognitive_load=cognitive_load,
        estimated_duration_minutes=duration,
        deadline=deadline_dt,
        priority=priority,
        status=TaskStatus.TODO,
        created_at=datetime.now(),
        time_window=time_window,
        not_before=not_before_dt,
        load_source=load_source,
        ai_load=ai_load,
        ai_duration_minutes=ai_duration_minutes,
        ai_confidence=ai_confidence,
        ai_rationale=ai_rationale,
        ai_model=ai_model,
    )
    db.add(task)
    db.commit()
    return _tasks_partial(request, db)


@app.put("/tasks/{task_id}", response_class=HTMLResponse)
def update_task(
    request: Request,
    task_id: int,
    title: str = Form(...),
    description: str = Form(""),
    cognitive_load: int = Form(...),
    estimated_duration_minutes: int = Form(None),
    deadline: str = Form(""),
    priority: str = Form(...),
    time_window: str = Form("any"),
    not_before: str = Form(""),
    ai_load: str = Form(""),
    ai_duration_minutes: str = Form(""),
    ai_suggested_duration: str = Form(""),
    ai_confidence: str = Form(""),
    ai_rationale: str = Form(""),
    ai_model: str = Form(""),
    db: Session = Depends(get_db),
):
    task = db.get(models.Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    duration = estimated_duration_minutes or scheduler.default_duration_for_load(cognitive_load)
    deadline_dt = datetime.fromisoformat(deadline) if deadline else None
    not_before_dt = datetime.fromisoformat(not_before) if not_before else None
    ai_load = int(ai_load) if ai_load else None
    ai_duration_minutes = int(ai_duration_minutes) if ai_duration_minutes else None
    ai_suggested_duration = int(ai_suggested_duration) if ai_suggested_duration else None
    ai_confidence = ai_confidence or None
    ai_rationale = ai_rationale or None
    ai_model = ai_model or None

    if ai_load is not None:
        task.load_source = _resolve_load_source_on_create(cognitive_load, duration, ai_load, ai_suggested_duration)
        task.ai_load = ai_load
        task.ai_duration_minutes = ai_duration_minutes
        task.ai_confidence = ai_confidence
        task.ai_rationale = ai_rationale
        task.ai_model = ai_model
    else:
        if task.load_source == "ai_accepted" and (
            cognitive_load != task.cognitive_load or duration != task.estimated_duration_minutes
        ):
            task.load_source = "ai_edited"

    task.title = title
    task.description = description or None
    task.cognitive_load = cognitive_load
    task.estimated_duration_minutes = duration
    task.deadline = deadline_dt
    task.priority = priority
    task.time_window = time_window
    task.not_before = not_before_dt
    db.commit()
    return _tasks_partial(request, db)


def _build_outcomes(db: Session) -> list[calibration.Outcome]:
    tasks = (
        db.query(models.Task)
        .filter(models.Task.status == TaskStatus.DONE, models.Task.completed_at.isnot(None))
        .order_by(models.Task.completed_at.desc())
        .limit(200)
        .all()
    )
    return [
        calibration.Outcome(
            title=t.title,
            load=t.cognitive_load,
            estimated_minutes=t.estimated_duration_minutes,
            ai_load=t.ai_load,
            ai_duration_minutes=t.ai_duration_minutes,
            actual_minutes=t.actual_minutes,
            hindsight_load=t.hindsight_load,
            load_source=t.load_source,
            completed_at=t.completed_at,
        )
        for t in tasks
    ]


@app.post("/tasks/estimate", response_class=HTMLResponse)
def estimate_task(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    answer: str = Form(""),
    db: Session = Depends(get_db),
    provider: llm_provider.LLMProvider = Depends(llm_provider.get_provider_dependency),
):
    settings = _get_plan_settings(db)
    outcomes = _build_outcomes(db)
    examples = calibration.select_examples(outcomes, title)
    try:
        suggestion = estimator.estimate(
            provider,
            title,
            description,
            answer,
            settings.about_me or "",
            examples,
            multiplier_for=lambda load: calibration.duration_multiplier(outcomes, load),
        )
    except LLMUnavailable:
        return templates.TemplateResponse(
            "_estimate_panel.html", {"request": request, "suggestion": None, "unavailable": True}
        )
    return templates.TemplateResponse(
        "_estimate_panel.html",
        {
            "request": request,
            "suggestion": suggestion,
            "unavailable": False,
            "load_anchors": scheduler.LOAD_ANCHOR,
        },
    )


@app.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
def complete_task(
    request: Request,
    task_id: int,
    actual_minutes: str = Form(""),
    hindsight: str = Form(""),
    db: Session = Depends(get_db),
):
    task = db.get(models.Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = TaskStatus.DONE
    task.completed_at = datetime.now()
    task.actual_minutes = int(actual_minutes) if actual_minutes else None
    if hindsight in ("easier", "right", "harder"):
        task.hindsight_load = calibration.hindsight_load(task.cognitive_load, hindsight)
    else:
        task.hindsight_load = None
    db.commit()
    return _tasks_partial(request, db)


@app.delete("/tasks/{task_id}", response_class=HTMLResponse)
def delete_task(request: Request, task_id: int, db: Session = Depends(get_db)):
    task = db.get(models.Task, task_id)
    if task:
        db.delete(task)
        db.commit()
    return _tasks_partial(request, db)


@app.post("/tasks/{task_id}/status", response_class=HTMLResponse)
def update_task_status(
    request: Request,
    task_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    task = db.get(models.Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if status == TaskStatus.IN_PROGRESS.value and not task.started_at:
        task.started_at = datetime.now()
    if status == TaskStatus.TODO.value and task.status == TaskStatus.DONE:
        task.completed_at = None
        task.actual_minutes = None
        task.hindsight_load = None
    task.status = status
    db.commit()
    if status == TaskStatus.DONE.value:
        return _tasks_partial(request, db, open_completion_for=task_id)
    return _tasks_partial(request, db)


def _completion_prefill(task: models.Task) -> int:
    if task.started_at:
        elapsed = int((datetime.now() - task.started_at).total_seconds() // 60)
        if 0 < elapsed < 480:
            return elapsed
    return task.estimated_duration_minutes


def _tasks_partial(request: Request, db: Session, open_completion_for: int | None = None):
    tasks = db.query(models.Task).order_by(models.Task.created_at.desc()).all()
    prefill = None
    if open_completion_for is not None:
        t = next((t for t in tasks if t.id == open_completion_for), None)
        if t:
            prefill = _completion_prefill(t)
    return templates.TemplateResponse(
        "_task_list.html",
        {
            "request": request,
            "tasks": tasks,
            "statuses": [s.value for s in TaskStatus],
            "open_completion_for": open_completion_for,
            "completion_prefill": prefill,
        },
    )


# ---------- Plan generation ----------


def _sync_task_statuses(db: Session, this_week_start: date) -> None:
    next_week_start = this_week_start + timedelta(days=7)
    placed_ids = {
        row[0]
        for row in db.query(models.PlanAssignment.task_id)
        .filter(models.PlanAssignment.week_start_date.in_([this_week_start, next_week_start]))
        .filter(models.PlanAssignment.unschedulable == False)  # noqa: E712
        .all()
    }
    tasks = (
        db.query(models.Task)
        .filter(models.Task.status.in_([TaskStatus.TODO, TaskStatus.SCHEDULED]))
        .all()
    )
    for t in tasks:
        t.status = TaskStatus.SCHEDULED if t.id in placed_ids else TaskStatus.TODO


@app.post("/plan/generate", response_class=HTMLResponse)
def generate_plan_route(
    request: Request,
    week: str = Form("this"),
    allow_split: bool = Form(False),
    db: Session = Depends(get_db),
):
    prefs = load_prefs(db)
    now = datetime.now()
    settings = _get_plan_settings(db)
    settings.allow_split = allow_split

    this_week_start = current_week_start(now.date())
    target_week_start = this_week_start if week == "this" else this_week_start + timedelta(days=7)

    events = _events_for_week(db, target_week_start)
    tasks_query = db.query(models.Task).filter(models.Task.status != TaskStatus.DONE).all()

    if week == "next":
        this_week_assignments = (
            db.query(models.PlanAssignment)
            .filter(models.PlanAssignment.week_start_date == this_week_start)
            .filter(models.PlanAssignment.unschedulable == False)  # noqa: E712
            .all()
        )
        placed_ids = set()
        for a in this_week_assignments:
            if a.slot_end is None or a.day_of_week is None:
                continue
            day_offset = DAY_ORDER.index(a.day_of_week.value)
            a_date = this_week_start + timedelta(days=day_offset)
            if datetime.combine(a_date, a.slot_end) > now:
                placed_ids.add(a.task_id)
        tasks = [t for t in tasks_query if t.id not in placed_ids]
        plan_now = datetime.combine(target_week_start, time.min)
    else:
        tasks = tasks_query
        plan_now = now

    task_ins = [_task_in(t) for t in tasks]
    event_ins = [_event_in(e) for e in events]

    assignments = scheduler.generate_plan(
        task_ins, event_ins, target_week_start, prefs=prefs, now=plan_now, allow_split=allow_split
    )

    if week == "this":
        db.query(models.PlanAssignment).filter(
            models.PlanAssignment.week_start_date == this_week_start
        ).delete()
        db.query(models.PlanAssignment).filter(
            models.PlanAssignment.week_start_date == this_week_start + timedelta(days=7)
        ).delete()
    else:
        db.query(models.PlanAssignment).filter(
            models.PlanAssignment.week_start_date == target_week_start
        ).delete()

    for a in assignments:
        db.add(
            models.PlanAssignment(
                week_start_date=target_week_start,
                task_id=a.task_id,
                day_of_week=a.day_of_week,
                slot_start=a.slot_start,
                slot_end=a.slot_end,
                residue_penalty_applied=a.residue_penalty_applied,
                override_reason=a.override_reason,
                reasoning_text=a.reasoning_text,
                unschedulable=a.unschedulable,
                split_label=a.split_label,
                factors_json=json.dumps(a.factors),
                warnings_json=json.dumps(a.warnings),
            )
        )

    db.flush()
    _sync_task_statuses(db, this_week_start)
    db.commit()
    return RedirectResponse(url=f"/plan?week={week}", status_code=303)


# ---------- LLM health ----------


@app.get("/api/llm/health")
def api_llm_health(provider: llm_provider.LLMProvider = Depends(llm_provider.get_provider_dependency)):
    from dataclasses import asdict

    return asdict(provider.health())


@app.get("/llm/badge", response_class=HTMLResponse)
def llm_badge(
    request: Request, provider: llm_provider.LLMProvider = Depends(llm_provider.get_provider_dependency)
):
    health = provider.health()
    return templates.TemplateResponse("_llm_badge.html", {"request": request, "health": health})


# ---------- JSON API ----------


@app.get("/api/events")
def api_list_events(db: Session = Depends(get_db)):
    return db.query(models.Event).all()


@app.get("/api/tasks")
def api_list_tasks(db: Session = Depends(get_db)):
    return db.query(models.Task).all()


@app.get("/api/plan/current")
def api_plan_current(week: str = "this", db: Session = Depends(get_db)):
    this_week_start = current_week_start()
    week_start = this_week_start if week == "this" else this_week_start + timedelta(days=7)
    rows = (
        db.query(models.PlanAssignment)
        .filter(models.PlanAssignment.week_start_date == week_start)
        .all()
    )
    out = []
    for a in rows:
        out.append(
            {
                "id": a.id,
                "week_start_date": a.week_start_date.isoformat(),
                "task_id": a.task_id,
                "day_of_week": a.day_of_week.value if a.day_of_week else None,
                "slot_start": a.slot_start.isoformat() if a.slot_start else None,
                "slot_end": a.slot_end.isoformat() if a.slot_end else None,
                "residue_penalty_applied": a.residue_penalty_applied,
                "override_reason": a.override_reason,
                "reasoning_text": a.reasoning_text,
                "unschedulable": a.unschedulable,
                "split_label": a.split_label,
                "factors": json.loads(a.factors_json) if a.factors_json else None,
                "warnings": json.loads(a.warnings_json) if a.warnings_json else None,
            }
        )
    return out
