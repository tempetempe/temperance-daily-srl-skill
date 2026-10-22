# Daily Page generation and persistence

## Input boundary

Generate Daily Page only after clear semantic progression intent and after required Coaching meets the shared-understanding and action conditions in [coaching-dialogue.md](coaching-dialogue.md): the current obstacle is clearer, relevant resources have been considered with the learner, and the learner has shaped a feasible approach with appropriate support. If emotion or overload affected the conversation, also confirm that the proposed load, timing, method, and interaction demand still fit what the learner chose; the emotion does not need to be resolved before planning. The generator reads the learner-saved Assessment and, only when needed, a Coaching/plan handoff keyed by stable Task ID. It validates data structure, not whether those conversational conditions occurred; the Agent must verify them before calling it. Use ordinary Chinese for learner-visible handoff values and page explanations.

Check these conditions separately for every task requiring Coaching. A confident task with no new concern needs no expanded Coaching; one completed task does not stand in for the remaining tasks. Before generation, compare the whole saved task list with the conversation, then proactively obtain any still-needed arrangement/progression confirmation. Do not wait for the learner to name the missing stage.

An explicit topic refusal or pause invokes “Emotional needs, refusal and pause” in `SKILL.md`. Preserve uncertainty, use confirmed facts for an accepted feasible arrangement, and do not invent clarification or require disclosure. “不知道”, distress, and not disclosing a private event do not themselves waive other Coaching. A pause suspends delivery; a learner continuing toward execution still receives the complete accepted Daily Page. The Agent must not defer remaining Coaching until after the first task merely because the learner is distressed.

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
    "coaching_provenance": [
      {
        "statement": "翻课堂笔记，标出还不懂的地方",
        "origin": "learner_initiated",
        "evidence": ["学习者：我想先翻课堂笔记，把还不懂的地方标出来。"]
      }
    ],
    "final_plan": {
      "planned_start": "19:00",
      "estimated_duration_minutes": 55
    }
  }
}
```

Include supported Coaching understanding and chosen actions even when the final action remains the same as the initial plan; clarification itself can be useful. Do not invent resources or actions to fill fields. A task needing no Coaching can have no handoff entry. An explicitly declined topic must not be recorded as completed clarification. Unknown Task IDs are rejected. Entry presence/absence does not prove Coaching occurred: inspect the conversation, not a self-authored completion flag.

For new coached handoffs, also retain source evidence for the decisive result items in `coaching_provenance` under the [Coaching source contract](coaching-dialogue.md#coaching-handoff). The example above additionally requires that quoted learner statement to have occurred. This single optional task field supports old data without it; do not manufacture provenance for old or uncoached tasks. At generation each `statement` must match a result item. It is an internal snapshot, not another editable Coaching input.

## Generate Runtime

Create a handoff only when a task has a confirmed Coaching Result or a confirmed final-plan override. With neither, omit the file and let the program use the saved Assessment as the initial plan. Confidence alone does not decide this: a confident task with a chosen time/order/text adjustment still needs its override. Never invent empty results or an unnecessary file solely for a handoff step.

With actual confirmed content, explicitly pass the file:

```bash
python3 <skill-package>/scripts/page_flow.py \
  --workspace <workspace-root> daily-page \
  --coaching-file /path/to/coaching-handoff.json
```

With no result and no override:

```bash
python3 <skill-package>/scripts/page_flow.py \
  --workspace <workspace-root> daily-page
```

The combined entry performs workspace readiness, generation/recovery and Bridge checks. Do not separately rerun ensure, generator or readiness on its normal successful path. It returns `status=ready`, `operation=generated|resumed`, `flow_date`, validated task/path information, `page_url`, and `page_opened=false`. The host still opens the page. Existing same-day content is resumed, never silently rebuilt; newly supplied handoff data is not reapplied on resume and the result says so. If a confirmed change must be applied to an existing learner page, preserve current State and use its editable page under the learner's current intent rather than force-generation.

Low-level `generate_daily_page.py` is retained for deliberate maintenance, not ordinary re-entry. Its options:

- `--force`: replace an existing Daily Page for the same date intentionally; not an option of the normal combined entry.
- `--refresh-html-only`: apply the current template to Runtime HTML while preserving the existing authoritative `daily-page-state.json`; use this for UI/template updates after a page may already contain learner edits.

Bootstrap and Assessment do not create Daily Page files. Only the clear Generate Daily Page stage creates them. The generator preserves Assessment Task IDs, maps underlying confidence to visual state, writes `coaching_result` at generation time, initializes empty Review/Task Note/Daily Diary fields, and installs this Runtime Pair with rollback protection:

- `.daily-srl/runtime/daily-page.html`
- `.daily-srl/runtime/daily-page-state.json`

## Page and Bridge

Open the combined entry's returned `page_url`; a printed URL alone is not delivery. The helper reuses a healthy same-workspace Bridge or starts/verifies one, with dynamic-port fallback. If content is ready but the Bridge fails, `status=page_not_ready` / `local_content_ready=true` preserves it; retry the combined entry to recover/reopen without generation. Standalone `bridge_readiness.py ... ensure --page daily-page` remains for recovery/diagnosis, not an automatic repeat after success. The learner never manages the server.

Before inviting execution, verify the generated State contains every saved Assessment task with the same IDs/date and faithfully represents selected actions. Verify the readiness result and that the host opened the correct Daily Page, not a template, Assessment, or old-day page. Existing helpers verify State and page access; they cannot certify dialogue quality or host display. A URL returned without a successful host open is not delivered. On failure, retain state and explain the remaining delivery problem plainly; do not say “开始吧” or ask the learner to manage the protocol.

Persistence uses:

- `GET /api/daily-page-state`
- `POST /api/daily-page-state`

The learner may edit all content shown in the page. Saving posts the complete page state and atomically replaces `daily-page-state.json`. Preserve internal `coaching_provenance` when editing, restoring drafts, or saving. The Bridge retains existing provenance if an older client omits it and rejects client additions, changes, or deletion of source evidence; it does so within the existing version/context checks. Visible Coaching edits do not rewrite the source snapshot.

Draft writes use `X-Daily-SRL-Save: draft` (also the default for older clients). Explicit Review submissions use `X-Daily-SRL-Save: review`; the Bridge rejects these if any task lacks completion, actual duration, or expectation. The optional `review_saved_at` is generated by the Bridge for a complete explicit submission. A later content-changing draft write clears it; an unchanged draft write preserves it. Read-only validation accepts older State without this field. The marker is not a record-finalization signal or permission to skip content checks.

The page serializes writes and retains an explicit submit request queued during auto-save. Later edits are collected for the next request, and an earlier response cannot claim newer unsaved edits are complete. Reload is disabled during a write. A failed write does not report success or automatically retry indefinitely.

Distinguish messages: incomplete draft “已暂存填写进度，复盘还未完成”; complete but unsubmitted draft “填写进度已暂存，可以点击‘保存今日复盘’完成提交”; validated explicit save “今日复盘已保存”. Do not call an incomplete draft a completed Review. Pausing needs no completed Review; formal assessment saving still requires all Assessment values. Saved timestamps alone do not establish completion.

Opening `templates/daily-page-template.html` directly enters a non-persistent preview mode. It demonstrates ordinary, notice, and focus cards; editing works, while save explains that disk persistence requires the generated Runtime page and Bridge. In formal Runtime, edits auto-save after a short delay, and the explicit “保存今日复盘” action remains available.

## State invariants

- `task_id` is identical to Assessment.
- `source_confidence` remains in data only.
- `visual_state` is `normal`, `notice`, or `focus` and controls learner-facing color/label.
- `coaching_result` is independent from `final_plan` and is never reconstructed after Review.
- Optional task-level `coaching_provenance` keeps the source snapshots supplied at generation. It is not rendered as an input or copied into `final_plan`; it must survive inspect, checkpoint, and final Structured Record creation. Missing legacy provenance stays missing.
- `final_plan` contains exactly task, planned start, and estimated duration—the same three editable fields shown in “今天的安排”. Steps, resources, help, reminders, and requirements belong only in the independent `coaching_result`, so the state cannot retain invisible duplicate values.
- Every new task initializes an editable `review` object containing completion, conditional completion details, actual duration, expectation comparison, `execution_process`, and `task_note`. The two free-text values are separate original answers. Legacy State may lack `execution_process`; do not derive it from `task_note`. “部分完成”, “未完成”, and “实际安排发生变化” use distinct prompts; “已完成” hides and clears the supplemental details. Auto-save may persist an incomplete Review while the learner is working; explicit Review save and Record creation require completion, actual duration, and expectation for every task.
- Page-level `daily_diary` stores the optional “今日小记”.
- Review Completed routing, Checkpoint, Further Discussion, and Record generation are documented in [review-records.md](review-records.md).
- Auto-saved or explicitly saved `daily-page-state.json` is the authoritative Final Plan and current page state.


## Learner-facing layout and Review fields

Each task card keeps the editable Plan and complete Review inputs together. The learner-facing “今天的安排” section always stays lightweight and consistent across visual states: show only task, planned start, and estimated duration. Do not render steps, resources, help, reminders, requirement information, a dashed divider, or a separate “执行补充” area beneath it; those would repeat the Coaching content.

For coached tasks, render the independent Coaching Result under the learner-facing title “关于这项任务”. Use natural field labels such as “目前最卡的地方”, “我们商量好的应对方式”, “手上能用的资源”, and “需要找谁帮忙，或要沟通什么”. Put the page-level optional Daily Diary below all task cards.

Daily Page shows soft green for ordinary tasks, a clean yellow `留意一下` card for Assessment `uncertain`, and a soft coral `重点关注` card for `not_confident`; do not mix the green accent into yellow cards, and never show the Assessment labels “有点悬” or “没把握” in the page UI.

## Review in Daily Page

Every task has exactly these fixed Review inputs:

- Completion: 已完成 / 部分完成 / 未完成 / 实际安排发生变化;
- Actual Duration;
- “和原先的预期相比，这项任务怎么样？” with 比预想更好 / 和预想差不多 / 没有达到预期;
- one optional unlimited actual-process answer under `review.execution_process`, with the fixed question “实际做这项任务时，你是怎么做的？和原来想的有什么不同吗？”;
- immediately after it, one optional unlimited Task Note under `review.task_note`, with the fixed question “关于这项任务，还有什么值得记下来的？”.

Do not add “感觉怎么样？”. Completion details are conditional: “部分完成” asks which part was not completed; “未完成” asks what happened; “实际安排发生变化” asks what changed; “已完成” hides and clears the supplemental field. Every task shows the same two text questions regardless of Coaching, confidence, completion, or expectation. The actual-process placeholder remains “可以说说用了什么方法或帮助、有没有临时换个做法，或者遇到什么没想到的情况。按原来的做法完成，也可以直接记录。”. The Task Note placeholder is “可以记下处理得不错的地方、新的发现、值得记住的经验，或者还没想明白的问题。”. These are optional cues, not separately required answers. Preserve each answer's wording and line breaks independently. No unusual event, special method, lesson, new insight, or long reflection is required; a not-started task can be described truthfully. Already-recorded content need not be repeated in the next box.

This fixed entry improves coverage without changing Review submission requirements. Empty or vague answers do not establish that a strategy was absent, used, or effective. No Coaching does not establish a previously formed method. When a method was not recorded before execution, a retrospective account remains retrospective; preserve it without treating it as an original prediction. Only clarify remaining details when needed for a current discussion, not to fill every analytic category.

Put the optional unlimited “今日小记” after all task cards. It is one free-text field for any overall experience, emotion, discovery, self-acknowledgement, lesson, confusion, unresolved issue, help/discussion request, message to the future self, or other expression. Keep it overall and open; do not require per-task lessons there or direct the learner to use it instead of task-level notes. Never turn it into a questionnaire.

Runtime auto-save may preserve an incomplete in-progress Review. The explicit “保存今日复盘” action and Review Checkpoint require Completion, Actual Duration, and Expectation for every task; actual process, Task Note, and Daily Diary remain optional.

For pre-RC2 State, keep `task_note` exactly where it is and display it as the existing open task note. The new process box starts empty when its key is absent; do not copy old answers into it or ask the learner to split history. HTML-only refresh itself does not add a key or rewrite State. When saving a page that omits `execution_process`, the Bridge retains an already-saved value; explicit edits to either text field remain independent. The RC2 Daily view identity prevents an old 7.0 page from saving its old question into the newly defined Task Note slot. Follow [recovery.md](recovery.md) for old-service and old-tab maintenance.

Distinguish draft preservation from formal Review completion. Incomplete auto-saved content is “已暂存填写进度，复盘还未完成”; complete draft content still needs submission or clear conversational Review Completed intent. `saved_at` is only a write time, not completion evidence. The page's optional `review_saved_at` marks a validated explicit page submission of the current content; subsequent content changes invalidate it. Always read and check required values even when a marker or a learner's “保存好了” exists. Never fabricate fields or block the learner from leaving because a draft is incomplete.



## RC7 shared recovery and submission boundary

Daily Page uses the same [save client and recovery contract](assessment-persistence.md#rc7-shared-save-recovery-assessment-and-daily-page) as Assessment. Keep its existing 900 ms coalesced autosave and manual-submission priority. Autosave posts `X-Daily-SRL-Save: draft`; only the explicit validated Review submission uses `review`. Browser draft restoration alone must never set `review_saved_at`, post an automatic Review, or route to record creation.

Preserve edits made during a delayed initial read or save response. A pending/cancelled/error request keeps its snapshot and current draft; clear queued automatic/manual attempts when the result is uncertain, then reconcile before another submission. An exception may trigger one scoped read, not an automatic save loop. Normal successful saves use their POST result directly. A complete draft still requires the learner's actual submission or existing conversational Review Completed intent.
