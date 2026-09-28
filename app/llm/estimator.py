"""AI cognitive-load/duration estimation. Builds the prompt and turns the
model's structured reply into a Suggestion the Tasks form can apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

from pydantic import BaseModel

from app.llm.provider import LLMProvider
from app.scheduler import LOAD_ANCHOR, LOAD_DEFAULT_DURATION


class LoadEstimate(BaseModel):  # field order is part of the contract
    rationale: str
    cognitive_load: Literal[1, 2, 3, 5, 8, 13]
    estimated_duration_minutes: int
    confidence: Literal["low", "medium", "high"]
    clarifying_question: str  # "" unless confidence is "low"


@dataclass
class HistoryExample:
    title: str
    load: int
    estimated_minutes: int
    actual_minutes: Optional[int]
    hindsight: Optional[str]  # "easier", "about right" or "harder"


@dataclass
class Suggestion:
    load: int
    duration_minutes: int  # after the multiplier
    raw_duration_minutes: int  # model output after clamping
    multiplier: float
    confidence: str
    rationale: str
    clarifying_question: str
    model: str


def round5(x: float) -> int:
    return 5 * round(x / 5)


_SYSTEM_PROMPT_TEMPLATE = """You estimate the cognitive load of one person's work tasks.

Cognitive load is working-memory demand: novelty, ambiguity and the number of
moving parts. It is not duration.

Pick exactly one level:
{scale_lines}

Rules:
- Write the rationale first, in one sentence.
- estimated_duration_minutes is focused working time for this person.
- Use the profile and past tasks to calibrate to this person.
- If the task is too vague to choose between non-adjacent levels, set
confidence to "low" and ask one short clarifying_question. Otherwise
clarifying_question is "".
- Reply with JSON only."""


def _system_prompt() -> str:
    scale_lines = "\n".join(
        f"{load}: {anchor}. Typical duration {LOAD_DEFAULT_DURATION[load]} min."
        for load, anchor in LOAD_ANCHOR.items()
    )
    return _SYSTEM_PROMPT_TEMPLATE.format(scale_lines=scale_lines)


def build_messages(
    title: str,
    description: str,
    answer: str,
    about_me: str,
    examples: list[HistoryExample],
) -> tuple[str, str]:
    system = _system_prompt()

    if examples:
        lines = "\n".join(
            f"- {e.title} | {e.load} | {e.estimated_minutes} | "
            f"{e.actual_minutes if e.actual_minutes is not None else '—'} | "
            f"{e.hindsight or '—'}"
            for e in examples
        )
        history = f"Past tasks (title | load | estimated min | actual min | in hindsight):\n{lines}"
    else:
        history = "Past tasks: none yet"

    user_parts = [
        f"About me: {about_me or 'not provided'}",
        "",
        history,
        "",
        "Task:",
        f"Title: {title}",
        f"Description: {description or 'none'}",
    ]
    if answer:
        user_parts.append(f"Clarification: {answer}")
    user = "\n".join(user_parts)

    return system, user


def estimate(
    provider: LLMProvider,
    title: str,
    description: str,
    answer: str,
    about_me: str,
    examples: list[HistoryExample],
    multiplier_for: Callable[[int], float] = lambda load: 1.0,
) -> Suggestion:
    system, user = build_messages(title, description, answer, about_me, examples)
    result = provider.generate_json(system, user, LoadEstimate)

    raw_duration = max(5, min(480, round5(result.estimated_duration_minutes)))
    multiplier = multiplier_for(result.cognitive_load)
    duration = max(5, min(480, round5(raw_duration * multiplier)))

    return Suggestion(
        load=result.cognitive_load,
        duration_minutes=duration,
        raw_duration_minutes=raw_duration,
        multiplier=multiplier,
        confidence=result.confidence,
        rationale=result.rationale,
        clarifying_question=result.clarifying_question,
        model=provider.model,
    )
