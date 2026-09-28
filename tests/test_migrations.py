import glob
import os

from sqlalchemy import create_engine, text

from app.migrations import migrate


def _build_legacy_schema(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR NOT NULL,
                    cognitive_load INTEGER NOT NULL
                )
                """
            )
        )
        conn.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY, title VARCHAR)"))
        conn.execute(
            text(
                "CREATE TABLE plan_assignments (id INTEGER PRIMARY KEY, task_id INTEGER)"
            )
        )
        conn.execute(text("CREATE TABLE plan_settings (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text("INSERT INTO tasks (id, title, cognitive_load) VALUES (1, 'Existing task', 5)")
        )
        conn.commit()
    return engine


def test_migrate_adds_columns_and_backs_up(tmp_path):
    db_path = str(tmp_path / "scheduler.db")
    engine = _build_legacy_schema(db_path)

    added = migrate(engine)
    assert len(added) > 0

    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()}
        assert "load_source" in cols
        assert "ai_load" in cols
        assert "time_window" in cols

        task = conn.execute(text("SELECT id, title, cognitive_load FROM tasks WHERE id=1")).fetchone()
        assert task == (1, "Existing task", 5)

        version = conn.execute(text("PRAGMA user_version")).scalar()
        assert version == 1

    backups = glob.glob(db_path + ".bak-*")
    assert len(backups) == 1


def test_migrate_twice_is_idempotent(tmp_path):
    db_path = str(tmp_path / "scheduler.db")
    engine = _build_legacy_schema(db_path)

    migrate(engine)
    second = migrate(engine)

    assert second == []
    backups = glob.glob(db_path + ".bak-*")
    assert len(backups) == 1
