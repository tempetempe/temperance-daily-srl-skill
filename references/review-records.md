# Review, Further Discussion, and records

5.0 retains root schema version 1 and the immutable nested `review_checkpoint`, with `render_version = 2` and content-integrity metadata for safe layout updates. Optional root `emotional_support` projects the day journal, including consolidated summaries. Read [support-records.md](support-records.md) at support-saving or recovery nodes. Support is consolidated at closure or explicit pause, not written on each reply; final rendering places it after Further Discussion. Known v1 layouts remain readable and are adopted only during an authorized same-day update after content checks and backup.

Emotional support is factual recording, not a demand for interpretation or appraisal: distinguish what was offered, accepted, actually tried, and reported. Do not use the discussion categories below to duplicate, evaluate, or extract a lesson from these events. Non-emotional learning reflection retains the selective protocol below. The chosen emotional-support branch permits Agent guidance; learner-originated-suggestion rules still apply to learning plans.

## Review completion gate

The Review UI lives in each Daily Page task card. Every task must have a completion value, actual duration, and expectation comparison before the learner explicitly saves the Review. Actual process, Task Note, and Daily Diary remain optional, unlimited free text.

Every task has the fixed actual-process question “实际做这项任务时，你是怎么做的？和原来想的有什么不同吗？” under `review.execution_process`, followed by “关于这项任务，还有什么值得记下来的？” under `review.task_note`. Keep the two original answers independent in inspection, checkpoint, final Structured Record, and readable Learning Record. Day-level `daily_diary` remains overall and open. Use reported methods, help, changes, or unexpected events as evidence only to the extent actually stated. Ordinary execution and no special method are valid accounts. Missing content is unreported, not “not used”. Without a pre-execution method record, a retrospective description cannot prove the method was planned beforehand.

In inspect facts and checkpoint tasks, `execution_process` is alongside `task_note`, matching the existing task-level record layout. Preserve the optional key only when present in State. Do not move or copy pre-RC2 `task_note` text, backfill a process field, or change an existing checkpoint. When a legacy record has no process key, its old readable rendering is unchanged so integrity checks and supported same-day finalization remain compatible.

Actual duration is a non-negative integer: zero can truthfully represent an unstarted task. Missing duration is not zero and remains incomplete. Estimated duration must still be positive. Follow the refusal/pause/closure boundaries in `SKILL.md`: incomplete Review prevents a completed record, not the learner's right to stop now.

Auto-save preserves drafts, not formal completion. `saved_at` is a write timestamp; optional `review_saved_at` indicates an explicit page submission of current content, not permission to finalize the day. Always validate required values. A complete saved draft plus clear conversational Review Completed intent may proceed through the checkpoint; do not force a redundant button click. Neither a timestamp nor “保存好了” alone bypasses checks.

Review Completed is a semantic conversation intent, not a page event or phrase match. After the learner clearly says the Daily Page Review is completed and saved, inspect the current saved facts using the formal read-only entry below. If required Review values are missing, do not create records yet. If the learner wants to complete Review now, return them to the page and name only the missing items; otherwise respect their pause.

## Read-only current Review inspection

```bash
python3 <skill-package>/scripts/review_records.py \
  --workspace <workspace-root> inspect
```

The JSON result contains `status`, `ready`, `checks`, `missing`, `errors` and `facts`. `status` is `ready`, `incomplete`, `missing` or `invalid`; only `ready=true` permits using it as complete Review evidence. `invalid` exits nonzero; a zero exit with `incomplete` or `missing` is not completion. Name only missing items when the learner wants to finish; invalid State is preserved for formal recovery.

`facts` contains `record_date`, `tasks`, and `daily_diary`. Each task retains the original `assessment`, independent `coaching_result`, learner-authoritative `final_plan`, `actual_performance`, `review`, and original `task_note`, under its stable ID. Original learner free text is returned intact, not paraphrased by another model. Use these facts for semantic discussion decisions instead of manually checking all engineering structure in two full files. Relevant current conversation still matters even when page notes are empty. Do not implement a keyword trigger or discard original free text.

When present in State, the same task also retains `coaching_provenance` unchanged in inspection and `review_checkpoint`, and hence in the final Structured Record. It is internal source evidence for the original result statements, not learner-facing commentary or execution evidence. Do not infer a source for legacy records without it or refresh it from Review. The learner-facing Learning Record continues to preserve the actual-process answer in its existing task-note section; source metadata is not rendered as a new child-facing section.

Inspection is read-only: it does not initialize the workspace, write records or freeze future edits. Formal checkpoint creation still re-reads and validates current State under its normal lock; do not cache the view across learner edits or treat an earlier ready result as writing success. It cannot prove conversational meaning, claim truth or discussion readiness on its own.

## Semantic trigger decision

Before running the checkpoint command, decide semantically whether Further Discussion is needed.

- Expectation Gap triggers: `better` or `below_expected`. `as_expected` does not trigger by itself.
- Learner Signal triggers: material meaning in actual process, Task Note, Daily Diary, or the current relevant conversation, such as positive evidence, difficult experience, emotion, discovery, lesson, confusion, unresolved problem, help request, self-negation, possible ongoing issue, or an expressed wish to discuss. A routine account of execution or the mere presence of either optional text does not require more discussion.
- Completion, duration difference, Coaching Result, and Final Plan are evidence used to understand a trigger, not independent trigger families.

Do not implement this decision with keyword or regular-expression matching. Interpret the text in context. A diary can mention a difficult word without asking for discussion, or express a meaningful concern without using any anticipated vocabulary.

A current request for support can trigger discussion even with empty notes and all expectations `as_expected`; do not ask the learner to re-enter it on the page. Do not reopen an already-addressed or declined topic just because it appeared earlier. Respect clear closure. Current chat does not change the saved Review snapshot; supported discussion additions are appended separately.

## Review Checkpoint

Run exactly one explicit decision:

```bash
python3 <skill-package>/scripts/review_records.py \
  --workspace <workspace-root> checkpoint --needs-discussion
```

or:

```bash
python3 <skill-package>/scripts/review_records.py \
  --workspace <workspace-root> checkpoint --no-discussion
```

The command refuses incomplete Review data and refuses to overwrite existing records. It stages both outputs before replacing either:

- `.daily-srl/structured-records/YYYY-MM-DD.json`
- `Learning Records/YYYY-MM-DD.md`

The Structured Record keeps the entire immutable Review snapshot under `review_checkpoint`. Each task retains separate Assessment, Coaching Result, Final Plan, Actual Performance, Review, and Task Note layers. The day-level Daily Diary is stored in that checkpoint as well.

If discussion is needed, both records end in `review_saved`; otherwise they are immediately `final`. Preserve the original Review promptly when discussion continues; support not yet consolidated can wait until that discussion closes. If the learner closes without discussion, add `--support-file <batch.json>` to checkpoint to save any new support in the same transaction. Do not call the support saver first. No new support means omit the option.

The helpers verify actual outputs and record consistency before reporting success. A completed successful current invocation establishes that both real outputs passed internal checks; continue without routine extra directory listing, file reread or standalone verify. Old/queued/running receipts, incomplete output and unknown write outcomes are not success. Interruption, recovery, suspected external edits or diagnosis can require formal verification. This local success does not prove cloud delivery. Never claim unrecorded chat support is already in the checkpoint.

## Selective Further Discussion

Start from the identified signal, using the other saved Review data only as context. Establish what actually happened before explaining an expectation gap. Help the learner notice real positive evidence, understand experience, retain a lesson, identify an adjustment, revise resource use, or mark a possible follow-up only when the conversation supports that result. One or two turns can be enough, and not every discussion needs a lesson, an adjustment, or every result category.

When the signal includes emotion, follow [emotional-support.md](emotional-support.md). Respond to the learner's present experience as well as its learning impact. Completing the task, naming a likely source, or deciding that an event was one-time does not prove that the learner no longer needs support. Let them choose whether to say more, consider support or a practical adjustment, or simply be heard and finish. Do not extract a positive reframe or future strategy merely to fill the record.

Only record `possible_follow_up` when the learner explicitly describes recurrence or long duration, multiple tasks on the current day show the same issue, or the current discussion clearly establishes that the issue may persist. Do not read prior records to make this judgment, and never promise automatic next-day tracking or reminders.

These conditions do not require a follow-up entry. Do not make a declined private topic into a future agenda unless the learner wants it retained for that purpose. Match the discussion title to the current concern rather than defaulting to task completion or success. Use facts already supplied without repeatedly asking for them; an insight already expressed earlier is not newly produced here. Invite the learner's interpretation when they want reflection; apply the same learner-led suggestion and subject boundaries as Coaching.

Separate evidence strength before making a causal claim:

- `observations`: what the learner explicitly said or today's data directly shows;
- `hypotheses`: plausible explanations that retain uncertainty;
- `confirmed_conclusions`: only conclusions supported strongly enough to state as confirmed.

Do not use exclusionary or stable-trait wording when the evidence is only a one-day signal. Another task's saved time or blank note does not prove uninterrupted attention or absence of difficulty. A directly reported successful strategy is a clue, not proof that a different task has no gap. Preserve tentative wording and the speaker; never turn the Agent's explanation into the learner's quote.

Compare actual time to that task's saved Final Plan estimate before making time claims. For example, an estimate of 15 minutes and an actual 18 minutes is three minutes longer even if the learner rates it “和预想差不多”. The rating and arithmetic describe different things; neither overrides the other or proves completion. Use the existing saved fields, not an invented summary value.

This comparison concerns learner-entered task time, not inferred emotional-support duration. Chat/file timestamps and tool waiting cannot establish how long a learner spent managing feelings. Unknown activity or simulated event times remain unknown. Respond to current frustration before challenging a sweeping self-judgment; do not center the reply on defending why the Agent asked a question.

Invite interpretation before declaring a strategy effective. An action first described in Review may have been independently chosen during execution; do not credit it as previously agreed Coaching. A learner saying “没办法，只能等” may express resignation, not relief; reflect only what they said and do not force a feeling label or another question when they want to finish. When a suggested action was not tried, do not impose another trial to vindicate it. Any new suggestion follows the current-topic permission rule. Describe possible benefit and effort tentatively; do not promise “专治”, “不费力气” or “一定有效”.

After the identified topics have received an appropriate response or the learner has declined further discussion, ask one brief natural question about anything else to discuss/retain and ending the review only if current closure is not already clear. A contextual wish to finish need not contain the word “保存”. A support-option choice, factual answer or future return is not also final-save intent. Continue if the learner has more to say. Finalize only after clear Final Save intent in that context, and do not require a duplicate confirmation when the learner has already clearly asked to finish and save.

## Further Discussion handoff and final save

If no discussion additions were formed and the learner clearly wants to finish after a valid checkpoint, omit the empty intermediate file and run:

```bash
python3 <skill-package>/scripts/review_records.py \
  --workspace <workspace-root> finalize --no-additions
```

`--no-additions` and `--discussion-file` are mutually exclusive and one is required. The no-additions option constructs the same validated empty discussion structure (`{"topics": [], "additional_notes": []}`) internally, then uses the existing transaction, immutable checkpoint and verification. It is not permission to discard already-formed unsaved topics/notes. Add `--support-file <batch.json>` when actual support still needs saving, even when discussion itself is empty. Never invent a lesson, strategy or emotional resolution to satisfy closure. Respecting refusal is sufficient; resolving every concern is not required.

When actual additions exist, create a handoff. Notes alone or a topic with observations but no lesson are valid. An explicit empty topics list remains compatible; missing, null or incorrectly typed topics is invalid.

For a discussion that did produce supported content:

```json
{
  "topics": [
    {
      "topic": "数学练习比预想顺利",
      "related_task_ids": ["task-20260902-001"],
      "observations": ["先看课堂例题后能够独立完成了两道同类型题"],
      "hypotheses": ["这次看例题可能提供了一些帮助，尚不能确定单独起作用的因素"],
      "positive_evidence": ["学习者说今天看过例题后做出了两道题"]
    }
  ],
  "additional_notes": []
}
```

Only include supported categories that the discussion actually produced. Check each claimed action against a learner statement or saved execution evidence; an accepted plan does not prove execution, and a favorable outcome does not prove the proposed method caused it. Preserve uncertainty when the source does not establish a cause. Write Agent-authored text in ordinary Chinese suitable for the learner's record; category keys and status markers remain internal.

The example requires those learner statements and that tentative discussion to have actually occurred. It deliberately omits a lesson and future adaptation: those cannot be inferred from a good outcome. For every added action, also locate the learner's choice; do not treat permission to save as agreement with an unrelated interpretation. No extra child-facing confirmation form is needed.

Before finalizing, verify that the learner's response actually requests closure. A short option selection or factual answer must not be repurposed as either final-save permission or confirmation of an unrelated conclusion. The finalizer validates record structure, integrity and rendering; it cannot establish conversational meaning or claim truth. `findings` remains accepted for older handoffs but should not hide certainty. Then run:

```bash
python3 <skill-package>/scripts/review_records.py \
  --workspace <workspace-root> finalize \
  --discussion-file /path/to/further-discussion.json
```

When support also needs consolidation, include `--support-file /path/to/support-batch.json` in that same command. Refer to the batch schema in support-records.md. Do not duplicate support in the discussion topics. Identical finalizer retries preserve the saved bytes; differing content after finalization is rejected. New support after a final record uses save-batch at its own closure, not another Review.

The finalizer:

- adds the independent `further_discussion` field to the Structured Record;
- renders `## 进一步聊到的内容` if actual additions exist, then the independent `## 情绪方面的支持` when support exists;
- changes the effective status to `final`;
- preserves the original `review_checkpoint` exactly;
- verifies the existing files and saves a recoverable pre-update copy before rebuilding generated layout;
- preserves original learner wording, but updates generated status text and chapter order rather than requiring the entire old Markdown as a byte prefix.

The new discussion may explain the Review but must never replace Assessment, Coaching Result, Final Plan, Actual Performance, Review wording, actual process, Task Note, or Daily Diary.

## Write failure and recovery

For both Checkpoint and Final Save, the writer stages both complete outputs before replacing either destination. If an ordinary write/replace error is caught after one destination has changed, it rolls that destination back so the pair remains at its previous state.

An abrupt stop between replacements is not a truly atomic cross-file transaction. Version 5.0 uses a checksummed transaction intent, immutable pre-update backups and a day lock for checkpoint, finalization and support batches. With a valid pending intent, use the formal support-event recover then verify commands; only recognized before/after contents can be recovered. If no intent exists, it is damaged, or files were independently changed, stop and preserve the mismatch for deliberate repair. See [support-records.md](support-records.md) and [recovery.md](recovery.md); never regenerate over independent edits or treat a backup as permission to discard them.

## Structured Record shape

```json
{
  "schema_version": 1,
  "record_date": "2026-09-02",
  "record_status": "review_saved",
  "review_checkpoint": {
    "saved_at": "timestamp",
    "source_state": {
      "assessment": {"schema_version": 1, "generated_at": "timestamp", "saved_at": "timestamp"},
      "daily_page": {"schema_version": 2, "generated_at": "timestamp", "saved_at": "timestamp"}
    },
    "tasks": [
      {
        "task_id": "task-20260902-001",
        "assessment": {
          "task": "数学练习",
          "planned_start": "19:00",
          "estimated_duration_minutes": 45,
          "confidence": "uncertain"
        },
        "coaching_result": null,
        "final_plan": {
          "task": "数学练习册 P31–35",
          "planned_start": "19:00",
          "estimated_duration_minutes": 55
        },
        "actual_performance": {
          "completion": "completed",
          "completion_details": "",
          "actual_duration_minutes": 48
        },
        "review": {"expectation": "better"},
        "execution_process": "先看课堂例题，再试着独立完成练习。",
        "task_note": "先看例题以后比预想顺利。"
      }
    ],
    "daily_diary": ""
  }
}
```

The example shows the factual core, not every metadata field. New records additionally contain `render_version: 2` and `integrity: {record_sha256, learning_sha256}`, computed by the helper. After Final Save, `record_status` is `final` and the root gains `further_discussion`; optional `emotional_support` follows the journal schema. No field inside `review_checkpoint` changes.

## Learning Record example

The learner-facing canonical record is `Learning Records/YYYY-MM-DD.md`; it uses Chinese labels and keeps the original Review text intact:

```markdown
# 2026-09-02 学习记录

## 任务 1｜数学练习册 P31–35

### 最初的安排与判断
- 预计用时：45 分钟
- 当时的把握：有点悬

### 最终安排
- 预计用时：55 分钟

### 实际情况
- 完成情况：部分完成
- 实际用时：48 分钟

### 复盘
- 与原先预期相比：没有达到预期

#### 实际做这项任务时，你是怎么做的？和原来想的有什么不同吗？
> 时间不够时，我先标记了不会的题和具体卡点。

#### 关于这个任务，还有什么想记下来的？
> 虽然没有全部做完，但我及时标记了卡点。

## 今日小记
> 今天整体有点累，但整理笔记比预想顺利。
```

If discussion follows, the original learner text remains unchanged. Generated status/ordering can be updated. The discussion section precedes any emotional-support section:

```markdown
## 进一步聊到的内容

### 数学练习没有全部完成

#### 今天做得不错的地方
- 发现时间不够后及时标记了卡点。

#### 经验或教训
- 部分完成时保留清楚的卡点，能让之后继续处理更容易。
```

The example reflection requires an actual learner-supported discussion; it is not a default lesson. When emotional support occurred, add the separate `## 情绪方面的支持` after this section using the consolidated factual summaries. Do not copy live questions or internal reasoning into it. With no discussion additions, omit the empty discussion section; with no support, omit the support section.


## Final local save → optional team-space sync

After internally verified `checkpoint --no-discussion`, finalization or a saved final same-day support update, follow [team-space-sync.md](team-space-sync.md). `review_saved`, drafts and incomplete pauses are not final uploads. A save command's sync handoff is an instruction for the next operation, not evidence of cloud success. Sync failures cannot roll back local records or rewrite Review/learner text.
