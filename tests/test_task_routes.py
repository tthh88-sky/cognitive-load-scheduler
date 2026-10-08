from app.llm.provider import FakeProvider, NullProvider, get_provider_dependency


def _override(app, provider):
    app.dependency_overrides[get_provider_dependency] = lambda: provider


def _clear_override(app):
    app.dependency_overrides.pop(get_provider_dependency, None)


def test_estimate_medium_confidence_swaps_fields(client):
    from app.main import app

    _override(
        app,
        FakeProvider(
            [
                '{"rationale":"Needs care","cognitive_load":5,"estimated_duration_minutes":120,'
                '"confidence":"medium","clarifying_question":""}'
            ]
        ),
    )
    try:
        r = client.post("/tasks/estimate", data={"title": "Build feature", "description": "", "answer": ""})
        assert r.status_code == 200
        assert "AI: 5" in r.text
        assert "120 min" in r.text
        assert 'name="ai_load"' in r.text
        assert 'id="load-select"' in r.text
    finally:
        _clear_override(app)


def test_estimate_low_confidence(client):
    from app.main import app

    _override(
        app,
        FakeProvider(
            [
                '{"rationale":"Unclear","cognitive_load":3,"estimated_duration_minutes":60,'
                '"confidence":"low","clarifying_question":"Which service is this for?"}'
            ]
        ),
    )
    try:
        r = client.post("/tasks/estimate", data={"title": "Fix thing", "description": "", "answer": ""})
        assert "Which service is this for?" in r.text
        assert 'name="ai_load"' not in r.text
    finally:
        _clear_override(app)


def test_estimate_with_completed_history(client):
    from app.main import app

    client.post("/tasks", data={"title": "Ledger schema migration", "cognitive_load": 5, "priority": "Medium"})
    task_id = client.get("/api/tasks").json()[0]["id"]
    client.post(f"/tasks/{task_id}/status", data={"status": "done"})
    client.post(f"/tasks/{task_id}/complete", data={"actual_minutes": "150", "hindsight": "harder"})

    provider = FakeProvider(
        [
            '{"rationale":"Schema work","cognitive_load":8,"estimated_duration_minutes":120,'
            '"confidence":"high","clarifying_question":""}'
        ]
    )
    _override(app, provider)
    try:
        r = client.post("/tasks/estimate", data={"title": "Ledger refunds schema", "description": "", "answer": ""})
        assert r.status_code == 200
        assert "AI: 8" in r.text
        _system, user = provider.calls[0]
        assert "Ledger schema migration | 5 | 120 | 150 | harder" in user
    finally:
        _clear_override(app)


def test_estimate_unavailable_shows_manual_message(client):
    from app.main import app

    _override(app, NullProvider("LLM disabled"))
    try:
        r = client.post("/tasks/estimate", data={"title": "Fix thing", "description": "", "answer": ""})
        assert "enter manually" in r.text.lower()
    finally:
        _clear_override(app)


def test_load_source_manual(client):
    client.post("/tasks", data={"title": "T1", "cognitive_load": 3, "priority": "Medium"})
    tasks = client.get("/api/tasks").json()
    assert tasks[0]["load_source"] == "manual"


def test_load_source_accepted_and_edited(client):
    client.post(
        "/tasks",
        data={
            "title": "T2",
            "cognitive_load": 5,
            "estimated_duration_minutes": 120,
            "priority": "Medium",
            "ai_load": 5,
            "ai_suggested_duration": 120,
        },
    )
    tasks = client.get("/api/tasks").json()
    accepted = next(t for t in tasks if t["title"] == "T2")
    assert accepted["load_source"] == "ai_accepted"

    client.post(
        "/tasks",
        data={
            "title": "T3",
            "cognitive_load": 8,
            "estimated_duration_minutes": 120,
            "priority": "Medium",
            "ai_load": 5,
            "ai_suggested_duration": 120,
        },
    )
    tasks = client.get("/api/tasks").json()
    edited = next(t for t in tasks if t["title"] == "T3")
    assert edited["load_source"] == "ai_edited"


def test_completion_flow(client):
    r = client.post("/tasks", data={"title": "Done me", "cognitive_load": 5, "priority": "Medium"})
    task_id = client.get("/api/tasks").json()[0]["id"]

    r = client.post(f"/tasks/{task_id}/status", data={"status": "done"})
    assert r.status_code == 200
    assert "completion-row" in r.text

    r = client.post(f"/tasks/{task_id}/complete", data={"actual_minutes": "90", "hindsight": "harder"})
    assert r.status_code == 200

    task = client.get("/api/tasks").json()[0]
    assert task["actual_minutes"] == 90
    assert task["hindsight_load"] == 8  # load 5 + harder = 8
    assert task["status"] == "done"


def test_completion_skip_stores_nulls(client):
    client.post("/tasks", data={"title": "Skip me", "cognitive_load": 3, "priority": "Medium"})
    task_id = client.get("/api/tasks").json()[0]["id"]
    client.post(f"/tasks/{task_id}/status", data={"status": "done"})
    client.post(f"/tasks/{task_id}/complete", data={"actual_minutes": "", "hindsight": ""})

    task = client.get("/api/tasks").json()[0]
    assert task["actual_minutes"] is None
    assert task["hindsight_load"] is None


def test_undo_done_clears_outcome(client):
    client.post("/tasks", data={"title": "Undo me", "cognitive_load": 3, "priority": "Medium"})
    task_id = client.get("/api/tasks").json()[0]["id"]
    client.post(f"/tasks/{task_id}/status", data={"status": "done"})
    client.post(f"/tasks/{task_id}/complete", data={"actual_minutes": "30", "hindsight": "easier"})

    client.post(f"/tasks/{task_id}/status", data={"status": "todo"})
    task = client.get("/api/tasks").json()[0]
    assert task["actual_minutes"] is None
    assert task["hindsight_load"] is None
    assert task["completed_at"] is None
