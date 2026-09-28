"""User scheduling preferences. Pure Pydantic + stdlib only -- no FastAPI,
SQLAlchemy or app/llm imports, so the engine and the web layer can both
depend on it without a cycle (see app/scheduler.py).
"""

from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEK: list[Day] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

ENERGY_PRESETS: dict[str, dict[int, float]] = {
    "morning": {
        9: 0.9, 10: 1.0, 11: 1.0, 12: 0.9, 13: 0.7, 14: 0.6, 15: 0.7,
        16: 0.8, 17: 0.7, 18: 0.6,
    },
    "afternoon": {
        9: 0.6, 10: 0.7, 11: 0.8, 12: 0.9, 13: 0.8, 14: 0.9, 15: 1.0,
        16: 1.0, 17: 0.9, 18: 0.8,
    },
    "flat": {h: 1.0 for h in range(9, 19)},
}


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


class DayModifier(BaseModel):
    energy_scale: float = Field(1.0, ge=0.3, le=1.2)
    max_deep_minutes: int | None = Field(None, ge=0, le=600)


class Preferences(BaseModel):
    workdays: list[Day] = Field(
        default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"]
    )
    workday_start: time = time(9, 0)
    workday_end: time = time(19, 0)
    energy_curve: dict[int, float] = Field(
        default_factory=lambda: dict(ENERGY_PRESETS["morning"])
    )
    residue_half_life_minutes: int = Field(20, ge=5, le=120)
    transition_buffer_minutes: int = Field(5, ge=0, le=30)
    stack_gap_minutes: int = Field(15, ge=0, le=60)
    deep_load_threshold: Literal[3, 5, 8] = 5
    max_deep_minutes_per_day: int = Field(240, ge=0, le=600)
    deep_break_minutes: int = Field(15, ge=0, le=60)
    min_session_deep_minutes: int = Field(30, ge=15, le=120)
    min_session_light_minutes: int = Field(15, ge=5, le=60)
    day_modifiers: dict[Day, DayModifier] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "Preferences":
        if _minutes(self.workday_start) % 15 != 0:
            raise ValueError("workday_start must be on a 15-minute boundary")
        if _minutes(self.workday_end) % 15 != 0:
            raise ValueError("workday_end must be on a 15-minute boundary")
        if self.workday_start >= self.workday_end:
            raise ValueError("workday_start must be before workday_end")

        if not self.workdays:
            raise ValueError("workdays must not be empty")
        if len(set(self.workdays)) != len(self.workdays):
            raise ValueError("workdays must not contain duplicates")
        self.workdays = [d for d in WEEK if d in set(self.workdays)]

        for hour, value in self.energy_curve.items():
            if not (0 <= hour <= 23):
                raise ValueError("energy_curve hours must be between 0 and 23")
            if not (0 <= value <= 1):
                raise ValueError("energy_curve values must be between 0 and 1")

        return self

    def energy_at(self, hour: int) -> float:
        """Nearest defined hour; ties go to the earlier hour."""
        best_hour = min(self.energy_curve, key=lambda h: (abs(h - hour), h))
        return self.energy_curve[best_hour]

    def day_scale(self, day: str) -> float:
        modifier = self.day_modifiers.get(day)
        return modifier.energy_scale if modifier else 1.0

    def deep_cap(self, day: str) -> int:
        modifier = self.day_modifiers.get(day)
        if modifier is not None and modifier.max_deep_minutes is not None:
            return modifier.max_deep_minutes
        return self.max_deep_minutes_per_day

    def is_deep(self, load: int) -> bool:
        return load >= self.deep_load_threshold
