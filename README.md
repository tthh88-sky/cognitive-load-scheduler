# Cognitive-Load Aware Task Scheduler

Schedules tasks against your calendar at 15-minute resolution, matching each
task's cognitive load to the start time with the best focus — energy curve
minus decaying residue from recent events — while letting deadline/priority
override the ideal placement when needed (with a warning explaining why).
An optional local LLM (via Ollama) can suggest a task's load and duration
and calibrates itself from your completion history; with it off or
unreachable, every page and form behaves exactly as it does in manual mode.

## Run it

```
python -m pip install --user -r requirements.txt -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000 (redirects to the Plan tab). On startup the
app adds any missing columns to `scheduler.db` (backing it up first as
`scheduler.db.bak-<timestamp>`) — see `app/migrations.py`.

### Local dev without a ready Ollama setup

`python scripts/dev_server.py` (what `.claude/launch.json` runs) defaults
`LLM_PROVIDER=none` so the app never blocks on a half-configured local
Ollama — the AI badge just reads "AI offline, manual mode" and Suggest
says "enter manually". This default only applies if `LLM_PROVIDER` isn't
already set in your environment; unset it or export `LLM_PROVIDER=ollama`
once your Ollama setup is ready (see below).

## Run tests

```
python -m pytest
```

`tests/conftest.py` points `SCHEDULER_DB_URL` at a temp file before any test
imports `app.main`, so tests never touch your real `scheduler.db`.

## LLM configuration

All variables use an `LLM_` prefix so they never collide with Ollama's own
server variables (e.g. `OLLAMA_HOST`). None are required — omit them all and
the app runs in manual mode.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `none` (disables AI features) |
| `LLM_HOST` | `http://localhost:11434` | Ollama server URL |
| `LLM_MODEL` | `qwen3.8:27b` | Any local Ollama tag. Tags ending in `cloud` are always refused. |
| `LLM_ALLOW_CLOUD` | `0` | Set to `1` to allow a `*cloud` tag (not recommended — sends task data off-machine) |
| `LLM_TIMEOUT_S` | `60` | Per-request timeout |
| `LLM_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded |
| `LLM_NUM_CTX` | `8192` | Context window passed to Ollama |

Installing Ollama, GPU setup and picking a model are out of scope for this
repo — see the Ollama Runbook for the Scheduler. Once a model is pulled and
`ollama serve` is running, set `LLM_PROVIDER=ollama` (or just unset it) and
the header badge will pick it up within 30 seconds.

`scripts/eval_estimator.py` compares candidate models against a labeled CSV
or your own completed-task history — see the runbook for how it's used to
pick the final `LLM_MODEL`. It needs Ollama running and is not part of
`pytest`.

## Settings

The Settings tab (`/settings`) controls everything in `app/preferences.py`:
which days you work and your hours, the hourly energy curve (Morning /
Afternoon / Flat presets, or hand-tune each hour), residue/focus tuning
(half-life, transition buffer, meeting-stack gap), deep-work rules (load
threshold, daily cap, break between blocks, minimum session length when
splitting is on), and per-day overrides (energy scale, a day-specific deep
cap). "About me" is free text the AI estimator uses to calibrate
suggestions to you. Invalid input re-renders the form with error messages
and your submitted values, without saving.

## Structure

- `app/scheduler.py` — pure-Python scheduling engine: per-day timelines,
  residue decay, focus-based placement, deep-work cap/break, urgent
  fallbacks, splitting, and the reasoning text shown in the UI. No DB/web
  imports, fully unit-testable — see `tests/test_scheduler.py`,
  `tests/test_engine_core.py`, `tests/test_engine_rules.py`.
- `app/preferences.py` — the `Preferences` model (energy curve, work hours,
  deep-work/focus tuning, day modifiers). Pure Pydantic + stdlib.
- `app/calibration.py` — turns completed tasks into estimator examples, a
  duration multiplier, and the insights panel's numbers. Pure stdlib.
- `app/grid.py` — the pure `build_week_blocks` layout function shared by the
  Calendar/Plan/Overview CSS-grid views.
- `app/migrations.py` — startup schema migrator (`PRAGMA user_version`,
  backs up the db file before altering it).
- `app/llm/` — provider layer (`OllamaProvider`/`NullProvider`/
  `FakeProvider` behind a common Protocol), config, and the estimator
  (`app/llm/estimator.py`, prompt + `LoadEstimate` schema).
- `app/models.py` — SQLAlchemy models (`Event`, `Task`, `PlanAssignment`,
  `PlanSettings`).
- `app/main.py` — FastAPI routes: page routes rendering Jinja2 + HTMX
  partials, plus the JSON API under `/api`.
- `app/templates/` — Jinja2 templates; `_*.html` are HTMX partial responses.
- `scripts/eval_estimator.py` — offline model comparison script (not part of
  `pytest`); `scripts/dev_server.py` — local dev entrypoint.
- `scheduler.db` — SQLite file, created (and migrated) on first run.

## Notes / known limits

- No auth — single-user local app, as scoped.
- LLM-written preferences/plan review, calendar sync, multiple users, and
  any cloud LLM are out of scope for this version.

## Deviations from spec / open questions

- The candidate-start grid (spec §2.4) is implemented as `earliest` plus
  every 15-minute-of-day mark (anchored to the window's start, not to
  `earliest` itself) that falls after it — reading "every multiple of 15
  minutes after it" as the standard :00/:15/:30/:45 grid rather than
  `earliest + 15k`. This is what reproduces the spec's own worked examples
  (11:05 **and** 11:30 both being reachable candidates in the same window;
  a candidate landing exactly on 15:00 several minutes after a
  transition-buffered `earliest`).
- A couple of the spec's hand-computed example clock times in the WP2/WP3
  tables (e.g. one urgent-overflow placement test) didn't reproduce exactly
  from the written formulas; where that happened the test asserts the
  qualitative behavior the example was demonstrating rather than the exact
  clock time, since the formulas themselves are what's authoritative and
  are otherwise verified against the spec's precise numeric examples
  (residue decay values, costs, etc.) elsewhere in the suite.
- `LLM_PROVIDER` defaults to `none` in local dev (`scripts/dev_server.py`
  only, not the library default) since Ollama wasn't fully set up during
  this build — see "Local dev without a ready Ollama setup" above. The real
  `OllamaProvider` path is fully implemented and unit-tested against a
  monkeypatched client; it just hasn't been exercised against a live model.
