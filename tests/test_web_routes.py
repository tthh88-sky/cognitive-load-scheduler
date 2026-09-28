from datetime import date, timedelta


def test_settings_round_trip(client):
    r = client.get("/settings")
    assert r.status_code == 200

    r = client.post(
        "/settings",
        data={
            "about_me": "Backend dev",
            "workdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "workday_start": "08:00",
            "workday_end": "18:00",
            "energy_9": "0.5",
            "residue_half_life_minutes": "30",
            "transition_buffer_minutes": "10",
            "stack_gap_minutes": "15",
            "deep_load_threshold": "5",
            "max_deep_minutes_per_day": "180",
            "deep_break_minutes": "15",
            "min_session_deep_minutes": "30",
            "min_session_light_minutes": "15",
        },
    )
    assert r.status_code in (200, 303)

    r = client.get("/settings")
    assert "08:00" in r.text
    assert "Backend dev" in r.text


def test_settings_invalid_input(client):
    r = client.post(
        "/settings",
        data={
            "workdays": ["Mon"],
            "workday_start": "18:00",
            "workday_end": "08:00",  # end before start -> invalid
        },
    )
    assert r.status_code == 400
    assert "error" in r.text.lower() or "Error" in r.text


def test_plan_week_choice_and_no_duplicate(client):
    client.post(
        "/tasks",
        data={"title": "Task A", "cognitive_load": 3, "priority": "Medium"},
    )
    r = client.post("/plan/generate", data={"week": "this"})
    assert r.status_code in (200, 303)

    r = client.get("/plan?week=this")
    assert r.status_code == 200

    r = client.post("/plan/generate", data={"week": "next"})
    assert r.status_code in (200, 303)
    r = client.get("/plan?week=next")
    assert r.status_code == 200


def test_one_off_event_filtered_by_week(client):
    this_week = date.today() - timedelta(days=date.today().weekday())
    next_week = this_week + timedelta(days=7)

    client.post(
        "/events",
        data={
            "title": "Special meeting",
            "day_of_week": "Mon",
            "start_time": "10:00",
            "end_time": "11:00",
            "event_type": "meeting",
            "date": next_week.isoformat(),
        },
    )

    r = client.get("/calendar?week=this")
    assert "Special meeting" not in r.text

    r = client.get("/calendar?week=next")
    assert "Special meeting" in r.text


def test_task_with_time_window_and_not_before(client):
    r = client.post(
        "/tasks",
        data={
            "title": "Focused work",
            "cognitive_load": 5,
            "priority": "Medium",
            "time_window": "afternoon",
            "not_before": "2026-01-06T14:00",
        },
    )
    assert r.status_code == 200

    r = client.get("/api/tasks")
    tasks = r.json()
    assert tasks[0]["time_window"] == "afternoon"
