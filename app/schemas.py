from datetime import date as date_
from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models import DayOfWeek, EventType, Priority, TaskStatus
from app.scheduler import LOAD_DEFAULT_DURATION


class EventBase(BaseModel):
    title: str
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    event_type: EventType = EventType.OTHER
    is_interruptible: bool = False
    date: Optional[date_] = None


class EventCreate(EventBase):
    pass


class EventOut(EventBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    cognitive_load: int = Field(..., description="1,2,3,5,8,13")
    estimated_duration_minutes: Optional[int] = None
    deadline: Optional[datetime] = None
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.TODO
    time_window: str = "any"
    not_before: Optional[datetime] = None

    def resolved_duration(self) -> int:
        if self.estimated_duration_minutes is not None:
            return self.estimated_duration_minutes
        return LOAD_DEFAULT_DURATION.get(self.cognitive_load, 60)


class TaskCreate(TaskBase):
    ai_load: Optional[int] = None
    ai_duration_minutes: Optional[int] = None
    ai_suggested_duration: Optional[int] = None
    ai_confidence: Optional[str] = None
    ai_rationale: Optional[str] = None
    ai_model: Optional[str] = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: Optional[str] = None
    cognitive_load: int
    estimated_duration_minutes: int
    deadline: Optional[datetime] = None
    priority: Priority
    status: TaskStatus
    created_at: datetime
    load_source: str
    ai_load: Optional[int] = None
    ai_duration_minutes: Optional[int] = None
    ai_confidence: Optional[str] = None
    ai_rationale: Optional[str] = None
    ai_model: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    actual_minutes: Optional[int] = None
    hindsight_load: Optional[int] = None
    time_window: str
    not_before: Optional[datetime] = None


class PlanAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    week_start_date: date_
    task_id: int
    day_of_week: Optional[DayOfWeek]
    slot_start: Optional[time]
    slot_end: Optional[time]
    residue_penalty_applied: float
    override_reason: Optional[str]
    reasoning_text: str
    unschedulable: bool
    split_label: Optional[str] = None
    factors: Optional[dict] = None
    warnings: Optional[list[str]] = None
