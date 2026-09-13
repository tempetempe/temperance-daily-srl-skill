# Review, Further Discussion, and records

5.0 retains root schema version 1 and the immutable nested `review_checkpoint`, with `render_version = 2` and content-integrity metadata for safe layout updates. Optional root `emotional_support` projects the day journal, including consolidated summaries. Read [support-records.md](support-records.md) at support-saving or recovery nodes. Support is consolidated at closure or explicit pause, not written on each reply; final rendering places it after Further Discussion. Known v1 layouts remain readable and are adopted only during an authorized same-day update after content checks and backup.

Emotional support is factual recording, not a demand for interpretation or appraisal: distinguish what was offered, accepted, actually tried, and reported. Do not use the discussion categories below to duplicate, evaluate, or extract a lesson from these events. Non-emotional learning reflection retains the selective protocol below. The chosen emotional-support branch permits Agent guidance; learner-originated-suggestion rules still apply to learning plans.

## Review completion gate

The Review UI lives in each Daily Page task card. Every task must have a completion value, actual duration, and expectation comparison before the learner explicitly saves the Review. Task Note and Daily Diary remain optional, unlimited free text.

Actual duration is a non-negative integer: zero can truthfully represent an unstarted task. Missing duration is not zero and remains incomplete. Estimated duration must still be positive. Follow the declining/pausing/closing boundaries in `SKILL.md`: incomplete Review prevents a completed record, not the learner's right to stop now.

Auto-save preserves drafts, not formal completion. `saved_at` is a write timestamp; optional `review_saved_at` indicates an explicit page submission of current content, not permission to finalize the day. Always validate required values. A complete saved draft plus clear conversational Review Completed intent may proceed through the checkpoint; do not force a redundant button click. Neither a timestamp nor “保存好了” alone bypasses checks.

Review Completed is a semantic conversation intent, not a page event or phrase match. After the learner clearly says the Daily Page Review is completed and saved, read both State files. If required Review values are missing, do not create records yet. If the learner wants to complete Review now, return them to the page and name only the missing items; otherwise respect their pause.

## Semantic trigger decision

Before running the checkpoint command, decide semantically whether Further Discussion is needed.

- Expectation Gap triggers: `better` or `below_expected`. `as_expected` does not trigger by itself.
- Learner Signal triggers: material meaning in Task Note, Daily Diary, or the current relevant conversation, such as positive evidence, difficult experience, emotion, discovery, lesson, confusion, unresolved problem, help request, self-negation, possible ongoing issue, or an expressed wish to discuss.
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

The helpers verify actual outputs and record consistency before reporting success. Do not add routine separate verify/read calls after every successful save. An interruption, recovery or diagnostic request can require an explicit verification. Never claim unrecorded chat support is already in the checkpoint.

## Selective Further Discussion

Start from the identified signal, using the other saved Review data only as context. Establish what actually happened before explaining an expectation gap. Help the learner notice real positive evidence, understand experience, retain a lesson, identify an adjustment, revise resource use, or mark a possible follow-up only when the conversation supports that result. One or two turns can be enough, and not every discussion needs a lesson, an adjustment, or every result category.

When the signal includes emotion, follow the cross-stage support guidance in `SKILL.md`. Respond to the learner's present experience as well as its learning impact. Completing the task, naming a likely source, or deciding that an event was one-time does not prove that the learner no longer needs support. Let them choose whether to say more, consider support or a practical adjustment, or simply be heard and finish. Do not extract a positive reframe or future strategy merely to fill the record.

Only record `possible_follow_up` when the learner explicitly describes recurrence or long duration, multiple tasks on the current day show the same issue, or the current discussion clearly establishes that the issue may persist. Do not read prior records to make this judgment, and never promise automatic next-day tracking or reminders.

These conditions do not require a follow-up entry. Do not make a declined private topic into a future agenda unless the learner wants it retained for that purpose. Match the discussion title to the current concern rather than defaulting to task completion or success. Use facts already supplied without repeatedly asking for them; an insight already expressed earlier is not newly produced here. Invite the learner's interpretation when they want reflection; apply the same learner-led suggestion and subject boundaries as Coaching.

Separate evidence strength before making a causal claim:

- `observations`: what the learner explicitly said or today's data directly shows;
- `hypotheses`: plausible explanations that retain uncertainty;
- `confirmed_conclusions`: only conclusions supported strongly enough to state as confirmed.

Do not use exclusionary or stable-trait wording when the evidence is only a one-day signal. Another task's saved time or blank note does not prove uninterrupted attention or absence of difficulty. A directly reported successful strategy is a clue, not proof that a different task has no gap. Preserve tentative wording and the speaker; never turn the Agent's explanation into the learner's quote.

Compare actual time to that task's saved Final Plan estimate before making time claims. For example, an estimate of 15 minutes and an actual 18 minutes is three minutes longer even if the learner rates it “和预想差不多”. The rating and arithmetic describe different things; neither overrides the other or proves completion. Use the existing saved fields, not an invented summary value.

This comparison concerns learner-entered task time, not inferred emotional-support duration. Chat/file timestamps and tool waiting cannot establish how long a learner spent managing feelings. Unknown activity or simulated event times remain unknown. Respond to current frustration before challenging a sweeping self-judgment; do not center the reply on defending why the Agent asked a question.

Invite interpretation before declaring a strategy effective. An action first described in Review may have been independently chosen during execution; do not credit it as previously agreed Coaching. A learner saying “没办法，只能等” may express resignation, not relief; reflect only what they said and do not force a feeling label or another question when they want to finish. When a suggested action was not tried, do not impose another trial to vindicate it. Any new suggestion follows the current-topic permission rule.

After the identified topics have received an appropriate response or the learner has declined further discussion, follow the natural closing rule in `SKILL.md`. Continue if the learner has more to say. Finalize only after clear Final Save intent in that context, and do not require a duplicate confirmation when the learner has already clearly asked to finish and save.

## Further Discussion handoff and final save

Create a temporary handoff such as:

If nothing was added and the learner clearly wants to finish after a valid checkpoint, use `{"topics": [], "additional_notes": []}`. An explicit empty topics list is valid; a missing, null, or incorrectly typed list is not. Notes alone or a topic with observations but no lesson are valid too. Never invent a topic, lesson, or emotional resolution to satisfy saving. Respecting a learner's refusal to discuss is sufficient; resolving every identified concern is not a closing prerequisite.

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

The new discussion may explain the Review but must never replace Assessment, Coaching Result, Final Plan, Actual Performance, Review wording, Task Note, or Daily Diary.

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
