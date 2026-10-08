# Demo video script (3:00 max)

Rubric targets: **Working solution** (input → AI → output, end-to-end),
**Meaningful use of AI** (AI enables the key step, with safeguards),
**UX & value** (easy to follow, clearly better).

Everything below was dry-run against the seeded data with `qwen3.5:9b`:
the AI replies quoted here are what it actually returned, identically on
repeated runs.

## Before you hit record

1. `ollama serve` is running.
2. `python scripts/seed_demo.py` (resets data; backs up the old db). Re-run it
   between takes.
3. `python scripts/dev_server.py`, then open http://localhost:8000.
4. **Warm the model**: on Tasks, type any title, click Suggest, and wait. The
   first call takes ~30 s to load the model; after that, replies come back in
   1–2 s. Reload the page afterwards.
5. The seed targets **next week**. Any deadline you type live must fall in
   that week (shown in the Plan header). If you record on or after that
   Monday, re-run the seed.
6. Browser at 16:9, zoomed so the plan grid's text is readable (~110–125%).
   Have the Calendar tab set to "Next week".

---

## 1. Context (0:00–0:20) · Calendar tab, Next week

> "Meet a backend engineer on a platform team. Every week they juggle a
> backlog against a calendar full of meetings, and they get it wrong in two
> ways: hard design work ends up in the gaps between meetings, and they
> always underestimate how long it takes. So deadlines slip on Thursday
> night."

Point at the standups and the packed Tuesday as you say "calendar full of
meetings".

## 2. Demo (0:20–2:20)

### a. The inputs (0:20–0:30) · Tasks tab

> "This is their backlog for next week: nine tasks, from a 15-minute email to
> a half-day API design, each with a deadline."

Scroll the list once. Don't stop on details.

### b. AI asks instead of guessing (0:30–1:00)

Type title **`Sort out the reports thing`** and click **Suggest**.

AI replies with a question (about fixing, creating, or investigating missing data).

> "I write it the way I'd write a sticky note. The AI doesn't guess a size.
> It asks what I actually mean."

Answer **`The weekly usage report CSV has wrong totals for deleted accounts since last week`**
and click **Re-estimate**.

AI replies: **3 · 60 min · high**, rationale about tracing a data discrepancy.

> "Now it's confident: a load-3 investigation, about an hour, and it tells me
> why."

Set the deadline to **Thursday 17:00** of the target week and click **Add Task**.

### c. AI calibrated to *this* person (1:00–1:35)

Title **`Design schema for soft-deleting accounts`**, description
**`Data model assumes hard deletes only; need soft delete with an audit trail and the ability to restore.`**
Click **Suggest**.

AI replies: **8 · 210 min · high**, *"adjusted +40% from your history"*.

> "Load 8, three and a half hours, adjusted plus 40% from my history. The
> model on its own said two and a half hours. But it has seen my last 15
> completed tasks, and on deep work I consistently run about 40% over. Its
> reasoning even points out that I underestimate schema changes."

Point at the **Insights** panel at the top:

> "And it's honest about its own track record: its load ratings matched my
> hindsight 11 times out of 15."

Set the deadline to **Friday 17:00** and click **Add Task**.

### d. The output: the week, planned (1:35–2:20) · Plan tab → Next week → Generate

> "One click to plan the week."

Walk through the grid in this order:

1. **Monday and Wednesday mornings**: "Deep work lands in peak-energy
   mornings, not straight after a meeting. Every block says why it's there."
2. **Tuesday**: "Tuesday is wall-to-wall meetings, so no deep work goes there."
3. **"Prioritized" block, Monday afternoon (bulk import API design)**: "This one
   couldn't get its ideal slot. It's due Thursday and there's no good morning
   left, so the deadline wins, and it warns me I'm over my 4-hour deep-work
   cap that day."
4. **Unschedulable panel (users table migration plan)**: "And the migration plan doesn't
   fit: it needs three and a half unbroken hours, and my longest gap is
   three. If the AI had said two and a half hours, it would have fit neatly
   and I'd have found out on Thursday night. Now I find out on Monday, and I
   can move the vendor demo or renegotiate the deadline."

## 3. Value (2:20–3:00)

> "What's better: planning goes from calendar Tetris to one click, and the
> plan respects how this person actually works. The AI does the parts rules
> can't: reading a vague task, asking when it's unsure, and learning from real
> completion times. The scheduling itself stays rule-based and explainable.
>
> Safeguards: the model runs locally, so task data never leaves the laptop.
> It asks rather than guesses. Every suggestion is editable before saving.
> And with the AI switched off, the app still works in manual mode.
>
> Next: import the calendar straight from Outlook, and turn a pasted email
> into tasks."

---

## Tips

- Cut any wait longer than a second in editing. The rubric doesn't reward
  watching a spinner.
- If you have time to spare, the strongest optional extra (about 15 s) is on
  Tasks: mark a task done → **Harder than expected**. Say that's the signal
  the +40% comes from.
- Don't claim numbers you haven't measured (e.g. "saves 20 minutes"). If you
  have a real before/after from testing, put it in the deck's Outcomes slide.
