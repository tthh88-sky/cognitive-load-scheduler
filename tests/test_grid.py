from datetime import time

from app.grid import build_week_blocks

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
OPEN = time(9, 0)
CLOSE = time(19, 0)


def _block(day, start, end):
    blocks = build_week_blocks(DAYS, OPEN, CLOSE, [(day, start, end, "event", object())])
    return blocks[0]


def test_hour_block():
    # 1 row per minute, row 2 = the first minute after open (09:00).
    b = _block("Mon", time(9, 0), time(10, 0))
    assert (b.row_start, b.row_span) == (2, 60)


def test_afternoon_block():
    b = _block("Mon", time(12, 5), time(13, 5))
    assert (b.row_start, b.row_span) == (2 + 185, 60)


def test_15_min_task():
    b = _block("Mon", time(10, 0), time(10, 15))
    assert (b.row_start, b.row_span) == (2 + 60, 15)


def test_clipped_at_close():
    # 18:30-19:30 clipped at the 19:00 close.
    b = _block("Mon", time(18, 30), time(19, 30))
    assert (b.row_start, b.row_span) == (2 + 570, 30)


def test_adjacent_blocks_never_overlap_even_off_15_min_grid():
    # A transition buffer can place blocks on non-15-minute boundaries
    # (e.g. 16:05, 16:20); back-to-back blocks must still render with no
    # shared rows, however they're offset from the day's open time.
    first = build_week_blocks(
        DAYS, OPEN, CLOSE, [("Mon", time(16, 5), time(16, 20), "task", object())]
    )[0]
    second = build_week_blocks(
        DAYS, OPEN, CLOSE, [("Mon", time(16, 20), time(17, 20), "task", object())]
    )[0]
    assert first.row_start + first.row_span == second.row_start


def test_column_by_day_index():
    b = _block("Wed", time(9, 0), time(10, 0))
    assert b.col == 4
