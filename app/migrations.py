"""Startup schema migrator, using SQLite's own PRAGMA user_version instead of
a migration framework (D7) -- the app is single-user, single-file, so a full
Alembic setup would be pure overhead. Always runs after
Base.metadata.create_all, so a brand-new database is already complete and
this is a no-op with no backup written.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

SCHEMA_VERSION = 1

NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("tasks", "load_source", "VARCHAR DEFAULT 'manual'"),
    ("tasks", "ai_load", "INTEGER"),
    ("tasks", "ai_duration_minutes", "INTEGER"),
    ("tasks", "ai_confidence", "VARCHAR"),
    ("tasks", "ai_rationale", "VARCHAR"),
    ("tasks", "ai_model", "VARCHAR"),
    ("tasks", "started_at", "DATETIME"),
    ("tasks", "completed_at", "DATETIME"),
    ("tasks", "actual_minutes", "INTEGER"),
    ("tasks", "hindsight_load", "INTEGER"),
    ("tasks", "time_window", "VARCHAR DEFAULT 'any'"),
    ("tasks", "not_before", "DATETIME"),
    ("events", "date", "DATE"),
    ("plan_assignments", "factors_json", "VARCHAR"),
    ("plan_assignments", "warnings_json", "VARCHAR"),
    ("plan_settings", "preferences_json", "VARCHAR"),
    ("plan_settings", "about_me", "VARCHAR"),
]


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def migrate(engine: Engine) -> list[str]:
    """Add every missing column, then set PRAGMA user_version.

    Idempotent. Returns the columns it added, as "table.column" strings.
    """
    with engine.connect() as conn:
        missing = [
            (table, column, ddl)
            for table, column, ddl in NEW_COLUMNS
            if column not in _existing_columns(conn, table)
        ]

        if missing:
            db_path = engine.url.database
            if db_path and os.path.exists(db_path) and os.path.getsize(db_path) > 0:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                shutil.copy2(db_path, f"{db_path}.bak-{stamp}")

            for table, column, ddl in missing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            conn.commit()

        conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
        conn.commit()

    return [f"{table}.{column}" for table, column, _ in missing]
