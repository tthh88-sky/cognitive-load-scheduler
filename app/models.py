import enum
import json

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
)
from sqlalchemy.orm import relationship

from app.database import Base


class DayOfWeek(str, enum.Enum):
    MON = "Mon"
    TUE = "Tue"
    WED = "Wed"
    THU = "Thu"
    FRI = "Fri"
    SAT = "Sat"
    SUN = "Sun"


DAY_ORDER = [
    DayOfWeek.MON,
    DayOfWeek.TUE,
    DayOfWeek.WED,
    DayOfWeek.THU,
    DayOfWeek.FRI,
    DayOfWeek.SAT,
    DayOfWeek.SUN,
]


class EventType(str, enum.Enum):
    MEETING = "meeting"
    COMMUTE = "commute"
    ON_SITE = "on_site"
    LUNCH = "lunch"
    OTHER = "other"


class Priority(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class TaskStatus(str, enum.Enum):
    TODO = "todo"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    day_of_week = Column(Enum(DayOfWeek), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    event_type = Column(Enum(EventType), nullable=False, default=EventType.OTHER)
    is_interruptible = Column(Boolean, nullable=False, default=False)
    date = Column(Date, nullable=True)  # one-off events only; null = recurring weekly


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    cognitive_load = Column(Integer, nullable=False)
    estimated_duration_minutes = Column(Integer, nullable=False)
    deadline = Column(DateTime, nullable=True)
    priority = Column(Enum(Priority), nullable=False, default=Priority.MEDIUM)
    status = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.TODO)
    created_at = Column(DateTime, nullable=False)

    # v2: AI estimation
    load_source = Column(String, nullable=False, default="manual")
    ai_load = Column(Integer, nullable=True)
    ai_duration_minutes = Column(Integer, nullable=True)
    ai_confidence = Column(String, nullable=True)
    ai_rationale = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)

    # v2: completion tracking
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    actual_minutes = Column(Integer, nullable=True)
    hindsight_load = Column(Integer, nullable=True)

    # v2: per-task constraints (D8)
    time_window = Column(String, nullable=False, default="any")
    not_before = Column(DateTime, nullable=True)

    assignments = relationship(
        "PlanAssignment", back_populates="task", cascade="all, delete-orphan"
    )


class PlanAssignment(Base):
    __tablename__ = "plan_assignments"

    id = Column(Integer, primary_key=True, index=True)
    week_start_date = Column(Date, nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    day_of_week = Column(Enum(DayOfWeek), nullable=True)
    slot_start = Column(Time, nullable=True)
    slot_end = Column(Time, nullable=True)
    residue_penalty_applied = Column(Float, nullable=False, default=0.0)
    override_reason = Column(String, nullable=True)
    reasoning_text = Column(String, nullable=False, default="")
    unschedulable = Column(Boolean, nullable=False, default=False)
    split_label = Column(String, nullable=True)
    factors_json = Column(String, nullable=True)
    warnings_json = Column(String, nullable=True)

    task = relationship("Task", back_populates="assignments")

    @property
    def factors(self) -> dict:
        return json.loads(self.factors_json) if self.factors_json else {}

    @property
    def warnings(self) -> list[str]:
        return json.loads(self.warnings_json) if self.warnings_json else []


class PlanSettings(Base):
    """Singleton row (id always 1) remembering the last-used Generate Plan
    options, so the form doesn't reset to defaults after every generate."""

    __tablename__ = "plan_settings"

    id = Column(Integer, primary_key=True, default=1)
    allow_split = Column(Boolean, nullable=False, default=False)
    preferences_json = Column(String, nullable=True)
    about_me = Column(String, nullable=True)
