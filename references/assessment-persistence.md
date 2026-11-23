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

After resolving the authorized Workspace Root, use the combined formal entry for normal generation and readiness:

```bash
python3 <skill-package>/scripts/page_flow.py \
  --workspace <workspace-root> assessment \
  --date YYYY-MM-DD \
  --task "数学练习册 P31–35" \
  --task "英语阅读"
```

Useful options:

- `--date YYYY-MM-DD`: generate for a specific local date;
- repeated `--task`: prefill task names in order;
- `--tasks-file PATH`: load a JSON array of prefill objects.

The combined entry has no `--force`: an existing same-day pair is safely recovered/reopened, not overwritten. Prefill inputs are not reapplied on resume; the result reports that fact. The low-level generator remains available for explicit maintenance only; its `--force` intentionally replaces current data and is not normal re-entry.

Bootstrap does not create either file. The clear Skill Entry that begins Assessment creates this pair. Generating a different date replaces fixed Runtime files automatically; normal same-date re-entry resumes the existing pair to avoid accidental loss of edits; deliberate low-level replacement requires `--force`. Pair installation stages both outputs and rolls back a normal partial failure. It creates or replaces only:

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

`page_flow.py` performs workspace readiness, generation or safe recovery, and initial Bridge checks internally. Other hosts use the default entry. Its `status=ready` result includes `operation=generated|resumed`, `flow_date`, validated task/path information, `page_url` and `page_opened=false`. The host must actually open that URL and verify the page content.

### WorkBuddy page handoff (Assessment and Daily Page)

1. Append `--bridge-mode workbuddy-persistent` to the normal page command when the current WorkBuddy task exposes a supported persistent terminal or execution session the Agent can use. Otherwise append `--bridge-mode workbuddy-detached`. Both modes return the prepared page's `flow_date` and `runtime_id`; a healthy Bridge may be reused.
2. For `needs_persistent_bridge`, run the returned `persistent_session.argv` in the supported session using its `env` values as additions to that session's authorized environment. Keep the foreground process running there. For `page_check_required`, the initial Bridge check has already run in the preparation command.
3. After the preparation command exits and any persistent launch reports startup, run the read-only handoff check in a **separate** execution invocation:

```bash
python3 <skill-package>/scripts/bridge_readiness.py --workspace <workspace-root> \
  verify-page --page assessment --date <flow_date> --runtime-id <runtime_id>
```

For Daily Page use `--page daily-page`. The check confirms the recorded process identity and tests the exact page generation, live State and shared save client. Use only its successful `page_url` for the handoff. Open it through the available WorkBuddy browser capability and verify the actual page displays the expected tasks. Wait for live State loading to finish successfully (for example, “已读取保存的内容。” or a successful draft restoration message). The page can display embedded task names while its live State is still loading or has failed. No periodic checks are needed.

If preparation returns `page_not_ready` with `local_content_ready=true`, or the subsequent check or browser open fails, preserve the current page inputs before closing or refreshing. Retry the same page and date once with `--recover-bridge <runtime_id>` from the original preparation result, preferring the supported persistent session if it is available. The retry resumes the original generation when it is still present, and can stop only the identified unhealthy Bridge for this workspace. It attempts to preserve the original port and reports when a different browser origin is required. Verify in a separate invocation again, then check the actual opened page. If the host cannot provide a working handoff, explain that the local page data is preserved and page saving is unavailable. Bridge startup and ports are internal; the learner does not have to run a server.

Standalone `bridge_readiness.py ... ensure --page assessment` remains available for targeted diagnosis. Invalid/conflicting Runtime stops safely.

Endpoints:

- `GET /api/assessment-state`: read and validate disk state;
- `POST /api/assessment-state`: validate and conditionally replace the complete state under the page-save lock;
- `GET /health`: Bridge health, Workspace identity, pair status, and State validity.

The Bridge binds to loopback only, accepts JSON writes with current page identity and revision, limits request size, validates the complete state, and uses temporary-file + `os.replace` writes. It does not use browser storage as authoritative state.

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


## RC7 shared save recovery (Assessment and Daily Page)

Both pages load `save-client.js` from the package via the same loopback Bridge. Normal load performs one State GET, with `ETag` and `X-Daily-SRL-Context` response headers. Normal save performs one POST carrying those exact revision/context values; success returns the saved State and new headers. The operational context is embedded in HTML and never enters learner State or final record schemas. Missing conditions return 428, changed generation/workspace 409, stale revision 412. Version check and write share a process/file lock; never rebase and replay stale data automatically.

Input events coalesce local browser drafts, with keys isolated by workspace, generation, date and page. Before closing, local draft flushing is attempted. A draft is not an Assessment submission, Review submission, local record, or team-space sync. Preview pages never persist. Browser storage can be unavailable or cleared, and cannot promise recovery across origins/ports, browsers or machines. Different drafts in multiple tabs share a guarded recovery slot: retain the existing other-tab draft and offer export of current inputs; do not claim every tab is automatically merged/recovered. Preserve an open page before changing its URL or refreshing. Failed initial load must not erase edits made while GET was pending.

Waiting has a learner-accessible cancel/recovery action, without a fixed request or whole-run deadline. Cancellation does not mean a write failed; retain inputs and unresolved submission evidence. Only after an error or explicit recovery, perform a scoped GET to compare the submitted snapshot and its saved marker. A matching unsaved Assessment is not evidence of a successful submission. A later response may confirm its snapshot but cannot overwrite newer edits. Conflict drafts remain available for deliberate reconciliation, never silent overwrites. Normal success needs no extra GET, recurring heartbeat, automatic retry loop or full-content cloud readback.

The Agent should use existing formal readiness/status/stop helpers to recover the same workspace, preferably retaining its port. If the verified service is older than `recoverable-save-v2`, preserve inputs, stop only that identified service with `bridge_readiness.py --workspace <root> stop`, then retry the normal `page_flow.py` entry. The entry refreshes an old page client from validated disk State without rewriting State or records. Do not reopen an old browser tab and assume its code was upgraded.

Both Windows and Mac have platform-specific startup options. Startup checks establish initial readiness. WorkBuddy recovery follows the [page handoff above](#workbuddy-page-handoff-assessment-and-daily-page), including cases where the process remains alive but loses access to workspace files. Use the host's authorized execution environment for persistent launch and preserve browser drafts before retrying. Do not invent a host API, bypass Job policy, elevate, install a system daemon, or loop restarts. If the host cannot sustain access, preserve drafts and explain the limited save availability.
