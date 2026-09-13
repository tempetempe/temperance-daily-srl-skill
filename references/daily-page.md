# Daily Page generation and persistence

## Input boundary

Generate Daily Page only after clear semantic progression intent and after required Coaching meets the shared-understanding and action conditions in `SKILL.md`: the current obstacle is clearer, relevant resources have been considered with the learner, and the learner has shaped a feasible approach with appropriate support. If emotion or overload affected the conversation, also confirm that the proposed load, timing, method, and interaction demand still fit what the learner chose; the emotion does not need to be resolved before planning. The generator reads the learner-saved Assessment and a Coaching handoff keyed by stable Task ID. It validates data structure, not whether those conversational conditions occurred; the Agent must verify them before calling it. Use ordinary Chinese for learner-visible handoff values and page explanations.

Check these conditions separately for every task requiring Coaching. A confident task with no new concern needs no expanded Coaching; one completed task does not stand in for the remaining tasks. Before generation, compare the whole saved task list with the conversation, then proactively obtain any still-needed arrangement/progression confirmation. Do not wait for the learner to name the missing stage.

An explicit topic refusal or pause invokes “Declining, pausing, and closing” in `SKILL.md`. Preserve uncertainty, use confirmed facts for an accepted feasible arrangement, and do not invent clarification or require disclosure. “不知道”, distress, and not disclosing a private event do not themselves waive other Coaching. A pause suspends delivery; a learner continuing toward execution still receives the complete accepted Daily Page. The Agent must not defer remaining Coaching until after the first task merely because the learner is distressed.

Before writing, locate the source statement for every claim and the learner's choice for every action. For learning arrangements, the learner must originate the initial approach unless they contextually requested suggestions; acceptance of an unsolicited complete plan does not repair that earlier violation. In the separately chosen emotional-support branch the Agent may guide support, but that does not authorize homework methods. Include only a genuinely chosen support-related arrangement needed for task execution in Coaching Result; save the actual support process through [support-records.md](support-records.md), not as a compulsory task or private story in the page. Unknown resources and execution-time choices remain conditional. A request to generate the page does not select all previously mentioned advice. These are semantic checks; adding a boolean “confirmed” cannot prove them.

Tasks sharing a learner-grounded obstacle may share dialogue, with remaining task-specific gaps handled separately. Do not duplicate resource questions merely to repeat the sequence; do not mistake one task's completed Coaching for evidence about a different obstacle. Clearly referred-to agreement for both actions can cover both tasks without two extra confirmation rounds.

Example Coaching handoff, valid only if the learner described the sticking point, confirmed the notebook is available, and proposed or explicitly chose the actions below. The example does not authorize the Agent to invent this plan or teach the subject:

```json
{
  "task-20260902-001": {
    "coaching_result": {
      "main_obstacles": ["学习者说函数图像这部分没听懂，还不清楚具体卡在哪里"],
      "actions": ["翻课堂笔记，标出还不懂的地方"],
      "resources": ["课堂笔记中的函数图像例题"],
      "help_or_communication": ["明天课前带着标出的地方问老师"],
      "other_confirmed_information": []
    },
    "final_plan": {
      "planned_start": "19:00",
      "estimated_duration_minutes": 55
    }
  }
}
```

Include supported Coaching understanding and chosen actions even when the final action remains the same as the initial plan; clarification itself can be useful. Do not invent resources or actions to fill fields. A task needing no Coaching can have no handoff entry. An explicitly declined topic must not be recorded as completed clarification. Unknown Task IDs are rejected. Entry presence/absence does not prove Coaching occurred: inspect the conversation, not a self-authored completion flag.

## Generate Runtime

```bash
python3 <skill-package>/scripts/generate_daily_page.py \
  --workspace <workspace-root> \
  --coaching-file /path/to/coaching-handoff.json
```

Useful options:

- `--force`: replace an existing Daily Page for the same date intentionally.
- `--refresh-html-only`: apply the current template to Runtime HTML while preserving the existing authoritative `daily-page-state.json`; use this for UI/template updates after a page may already contain learner edits.

Bootstrap and Assessment do not create Daily Page files. Only the clear Generate Daily Page stage creates them. The generator preserves Assessment Task IDs, maps underlying confidence to visual state, writes `coaching_result` at generation time, initializes empty Review/Task Note/Daily Diary fields, and installs this Runtime Pair with rollback protection:

- `.daily-srl/runtime/daily-page.html`
- `.daily-srl/runtime/daily-page-state.json`

## Page and Bridge

```bash
python3 <skill-package>/scripts/bridge_readiness.py \
  --workspace <workspace-root> ensure --page daily-page
```

Open the returned `page_url`. The readiness helper normally reuses the verified Bridge for the same Workspace; otherwise it starts and verifies one, including dynamic-port fallback. The learner never needs to manage it.

Before inviting execution, verify the generated State contains every saved Assessment task with the same IDs/date and faithfully represents selected actions. Verify the readiness result and that the host opened the correct Daily Page, not a template, Assessment, or old-day page. Existing helpers verify State and page access; they cannot certify dialogue quality or host display. A URL returned without a successful host open is not delivered. On failure, retain state and explain the remaining delivery problem plainly; do not say “开始吧” or ask the learner to manage the protocol.

Persistence uses:

- `GET /api/daily-page-state`
- `POST /api/daily-page-state`

The learner may edit all content shown in the page. Saving posts the complete page state and atomically replaces `daily-page-state.json`.

Draft writes use `X-Daily-SRL-Save: draft` (also the default for older clients). Explicit Review submissions use `X-Daily-SRL-Save: review`; the Bridge rejects these if any task lacks completion, actual duration, or expectation. The optional `review_saved_at` is generated by the Bridge for a complete explicit submission. A later content-changing draft write clears it; an unchanged draft write preserves it. Read-only validation accepts older State without this field. The marker is not a record-finalization signal or permission to skip content checks.

The page serializes writes and retains an explicit submit request queued during auto-save. Later edits are collected for the next request, and an earlier response cannot claim newer unsaved edits are complete. Reload is disabled during a write. A failed write does not report success or automatically retry indefinitely.

Distinguish messages: incomplete draft “已暂存填写进度，复盘还未完成”; complete but unsubmitted draft “填写进度已暂存，可以点击‘保存今日复盘’完成提交”; validated explicit save “今日复盘已保存”. Do not call an incomplete draft a completed Review. Pausing needs no completed Review; formal assessment saving still requires all Assessment values. Saved timestamps alone do not establish completion.

Opening `templates/daily-page-template.html` directly enters a non-persistent preview mode. It demonstrates ordinary, notice, and focus cards; editing works, while save explains that disk persistence requires the generated Runtime page and Bridge. In formal Runtime, edits auto-save after a short delay, and the explicit “保存今日复盘” action remains available.

## State invariants

- `task_id` is identical to Assessment.
- `source_confidence` remains in data only.
- `visual_state` is `normal`, `notice`, or `focus` and controls learner-facing color/label.
- `coaching_result` is independent from `final_plan` and is never reconstructed after Review.
- `final_plan` contains exactly task, planned start, and estimated duration—the same three editable fields shown in “今天的安排”. Steps, resources, help, reminders, and requirements belong only in the independent `coaching_result`, so the state cannot retain invisible duplicate values.
- Every task initializes an editable `review` object containing completion, conditional completion details, actual duration, expectation comparison, and one free-form task note. “部分完成”, “未完成”, and “实际安排发生变化” use distinct prompts; “已完成” hides and clears the supplemental details. Auto-save may persist an incomplete Review while the learner is working; explicit Review save and Record creation require completion, actual duration, and expectation for every task.
- Page-level `daily_diary` stores the optional “今日小记”.
- Review Completed routing, Checkpoint, Further Discussion, and Record generation are documented in [review-records.md](review-records.md).
- Auto-saved or explicitly saved `daily-page-state.json` is the authoritative Final Plan and current page state.
