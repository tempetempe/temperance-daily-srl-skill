# Assessment and Persistence

## Scope

Formal Assessment and its local persistence stay limited to four learner-facing fields per task:

1. task;
2. planned start/time;
3. estimated duration;
4. confidence: 有把握 / 有点悬 / 没把握.

Do not add obstacle, emotion, meaning, resource, completion-standard, or “why” fields. Those belong to conversation rather than the Assessment form. A meaningful emotional signal in the entry message may receive brief support before or alongside Assessment as described in `SKILL.md`; it must not be ignored merely because the form stays lightweight.

The rendered learner interface must be entirely Chinese. Engineering identifiers and schema keys may use English, but learner-visible titles, labels, buttons, placeholders, status, validation, and error feedback may not.

Opening `templates/assessment-template.html` directly enters a non-persistent preview mode. Adding, deleting, and editing tasks work so the layout can be reviewed, while the page clearly explains that disk saving requires the generated Runtime page and Bridge.

## Generate a runtime Assessment

After resolving and ensuring the authorized Workspace Root, invoke the generator from the installed Skill package:

```bash
python3 <skill-package>/scripts/generate_assessment.py \
  --workspace <workspace-root> \
  --task "数学练习册 P31–35" \
  --task "英语阅读"
```

Useful options:

- `--date YYYY-MM-DD`: generate for a specific local date;
- repeated `--task`: prefill task names in order;
- `--tasks-file PATH`: load a JSON array of prefill objects;
- `--force`: intentionally replace an existing runtime for the same date.

Bootstrap does not create either file. The clear Skill Entry that begins Assessment creates this pair. Generating a different date replaces fixed Runtime files automatically; same-date replacement requires `--force` to avoid accidental loss of current edits. Pair installation stages both outputs and rolls back a normal partial failure. It creates or replaces only:

- `.daily-srl/runtime/assessment.html`
- `.daily-srl/runtime/assessment-state.json`

It ensures the two long-term record directories exist but never edits their contents.

`--tasks-file` accepts objects with these optional prefill keys:

```json
[
  {
    "task": "数学练习册 P31–35",
    "planned_start": "19:00",
    "estimated_duration_minutes": 45,
    "confidence": "有点悬"
  }
]
```

## Make the page ready

```bash
python3 <skill-package>/scripts/bridge_readiness.py \
  --workspace <workspace-root> ensure --page assessment
```

Open the returned `page_url`. This helper checks health, recovers safe Runtime states, starts the Bridge automatically, uses 8977 when available, selects a dynamic port on conflict, verifies the pair and endpoint, and then returns. These are internal mechanics, not learner instructions. Direct `persistence_bridge.py` invocation remains a development diagnostic only.

Endpoints:

- `GET /api/assessment-state`: read and validate disk state;
- `POST /api/assessment-state`: validate and atomically replace the complete state;
- `GET /health`: Bridge health, Workspace identity, pair status, and State validity.

The Bridge binds to loopback only, accepts same-origin JSON writes, limits request size, validates the complete state, and uses temporary-file + `os.replace` writes. It does not use browser storage as authoritative state.

## State rules

- Every task ID has the form `task-YYYYMMDD-NNN`.
- Text edits never change the ID.
- Adding a task consumes `next_task_number`.
- Deleting a task does not decrement or reuse the counter.
- Page save sends the complete current task array, so deleted tasks disappear from disk and all learner edits are preserved.
- Page load reads the disk state and rebuilds the complete UI.
- The generated HTML embeds the Initial State for rendering and narrowly proven HTML-only recovery; it never outranks a learner-saved disk State.

## Agent handoff

After the learner saves, read:

```text
.daily-srl/runtime/assessment-state.json
```

Treat the saved task text, schedule, duration, confidence, and task IDs as authoritative. Do not infer deleted tasks from the original prompt or embedded page data.
