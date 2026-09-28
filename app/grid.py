"""Pure CSS-grid layout for the week views (Calendar/Plan/Overview), at
1-minute row resolution. Blocks are laid out on whole-minute boundaries
(not just :00/:15/:30/:45) because the engine's transition buffer can shift
a placement to e.g. 16:05 or 16:20 -- a 15-minute row grid can't represent
those boundaries exactly, and independently "growing" each adjacent block
out to the nearest 15-minute line makes back-to-back blocks claim the same
row. Per-minute rows have no rounding, so adjacent blocks never collide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from typing import Any


def _m(t: time) -> int:
    return t.hour * 60 + t.minute


@dataclass
class Block:
    col: int  # CSS grid column; column 1 holds time labels, so days start at 2
    row_start: int  # CSS grid row; row 1 is the header. One row per minute.
    row_span: int
    kind: str  # "event" or "task"
    item: Any
    overlap: bool  # task blocks with an overlaps_interruptible warning


def _has_interruptible_warning(item: Any) -> bool:
    warnings = getattr(item, "warnings", None)
    if warnings is None:
        raw = getattr(item, "warnings_json", None)
        if raw:
            try:
                warnings = json.loads(raw)
            except (TypeError, ValueError):
                warnings = []
        else:
            warnings = []
    return any("interruptible" in w for w in (warnings or []))


def build_week_blocks(
    days: list[str],
    open_t: time,
    close_t: time,
    items: list[tuple[str, time, time, str, Any]],
) -> list[Block]:
    open_m = _m(open_t)
    close_m = _m(close_t)
    blocks: list[Block] = []
    for day, start, end, kind, item in items:
        if day not in days:
            continue
        s = max(_m(start), open_m)
        e = min(_m(end), close_m)
        if e <= s:
            continue
        row_start = 2 + (s - open_m)
        row_span = max(1, e - s)
        col = 2 + days.index(day)
        overlap = kind == "task" and _has_interruptible_warning(item)
        blocks.append(
            Block(col=col, row_start=row_start, row_span=row_span, kind=kind, item=item, overlap=overlap)
        )
    return blocks
