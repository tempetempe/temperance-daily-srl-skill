# Interruption and recovery

## 5.0 consolidated support and versioned records

Before the stage rules below, read [support-records.md](support-records.md) and inspect only the selected learning date with the formal support `verify` / `show` commands. A valid `.daily-srl/support-events/YYYY-MM-DD.json` preserves support already saved, including before Assessment, without implying Review or Coaching completion. Use its factual choices and latest feedback, not a guessed emotional state; clarify current wishes only where needed. A journal alone is not permission to start a new Assessment or resume a declined topic. Its phase describes where support occurred, not which stages are complete.

If a `.pending.json` intent exists, normal record writes are blocked. Use `support_events.py ... recover`, followed by `verify`; it completes only a recognized interrupted write whose current bytes still match the saved before/after hashes. If the intent is missing, invalid, or files were changed independently, preserve the mismatch and stop for diagnosis. Never hand-edit JSON or delete the intent to bypass the protection. This formal recovery exception precedes the legacy no-intent mismatch rules below.

The table's chat-only limits apply to content not actually saved. Normal 5.0 support is consolidated at closure or explicit pause, not at every turn. A sudden stop can leave recent facts only in the host conversation; use only currently available authorized evidence and clarify needed gaps, never fabricate missing history. Existing batches survive new conversations and Runtime replacement. Do not read prior-day journals for strategy selection. Recovery retries the same input/IDs; it is not new support. Even final may receive a same-day support batch at a later support closure without reopening Review or returning the learner to homework.

Version 2 generated records verify content integrity and full rendering. Known v1 records require exact recognition of their legacy layout before a same-day update; unrecognized or edited files stop safely. The helper backs up pre-update bytes in `.daily-srl/record-backups/YYYY-MM-DD/` and can reorganize generated sections, never the nested Review or original learner words. A changed Markdown or structured file is not automatically “repaired” from the other. Backups preserve evidence, not authority to overwrite user edits. New or corrected facts use new event/summary revisions; old facts are not silently replaced.

## Recovery authority

Recover from validated, current-date artifacts rather than conversational guesses:

1. the paired Structured/Learning Records when both are `final`;
2. the paired Review Checkpoint when both are `review_saved`;
3. `daily-page-state.json` for the learner's Final Plan and any autosaved Review work;
4. `assessment-state.json` for the saved Assessment;
5. a fresh Skill Entry only when no valid current-date artifact exists.

Always compare the date and Task IDs before resuming. A vague “好了” or “继续” does not override the artifact stage or satisfy a semantic progression gate.

Before applying this flow-stage precedence, ensure the authorized Workspace and inspect each expected Runtime Pair. State-only recovery re-renders HTML without changing State. HTML-only recovery is allowed only when the formal helper proves the embedded Initial State was never successfully edited; otherwise stop as incomplete. `user_saved=true` and unknown interaction state both mean STOP / PRESERVE. Never parse HTML or manually recreate JSON. Damaged State is always preserved. See [workspace-runtime.md](workspace-runtime.md) for the full pair matrix.

## Recovery by interruption point

| Interruption point | Durable content | Resume point | Deliberate limit |
| --- | --- | --- | --- |
| Assessment saved | Complete Assessment and stable Task IDs | Check all tasks, resume missing necessary Coaching, then proactively offer page generation if not already authorized | Neither Coaching completion nor later stages are implied by saved Assessment |
| Coaching in progress | Saved Assessment | Check each relevant task, use available current conversation and reconfirm only needed understanding and current wishes | Unconfirmed chat-only Coaching or emotional context is not durable; do not assume another task or window completed it |
| Final Plan saved | Daily Page State, including confirmed Coaching Result | Plan/execution/Review on the same page | Real-world execution progress is not tracked automatically |
| During execution | Same Daily Page State | Check current need; continue, adjust, or pause as the learner wishes | The Skill does not infer completion from elapsed time |
| Learner paused | Only artifacts whose saving has been verified | Resume from validated state and current wishes; reconfirm only needed chat-only content | Pausing is not Final Save; missing Review fields must not be fabricated or demanded just to leave |
| Review filled | Autosaved Review, Task Note, and Diary that reached the Bridge | Finish missing Review fields or confirm Review Completed | An edit made immediately before a hard browser/process stop may precede the autosave debounce |
| Review Checkpoint saved | Both records through Review | Further Discussion if status is `review_saved`, otherwise the day is final | Never recreate or overwrite the checkpoint |
| Further Discussion in progress | Both `review_saved` records | Resume from the saved Review signals, check what the learner needs now, and reconfirm any result to retain | Unconfirmed chat-only discussion or emotional context is not durable |

After resuming pre-execution planning, follow the whole-session delivery rules in `SKILL.md`: missing Coaching is handled per task, explicit topic refusals retain uncertainty, and the complete accepted Daily Page is generated/recovered, verified, and opened before inviting execution. A pause does not waive these steps or require restarting completed work. Do not wait for the learner to discover a missing page. If they already began independently, acknowledge actual progress and help complete the remaining planning/delivery without pretending it happened earlier.

## Missing or invalid State

- A missing expected State endpoint returns `404`; do not fabricate an empty State or records. An HTML-only pair is incomplete unless the narrow untouched-initial-state proof succeeds.
- Invalid JSON or invalid schema returns an error; preserve the file bytes for diagnosis and do not overwrite it automatically.
- A missing `runtime/` directory may be recreated empty, but this does not establish any current flow stage.
- A missing `.daily-srl/structured-records/` or visible `Learning Records/` directory may be recreated independently; never change history in the other Records directory.
- A Daily Page cannot become a Review Checkpoint unless both State files validate, dates and ordered Task IDs match, both have been saved, and every task has the required Review values.
- Generating a new day's Runtime may replace only Runtime files. It must not read prior-day history by default and must not delete or edit either long-term records directory.

## Paired-record consistency

Normal write failures are handled as a pair: both complete outputs are staged first, and a caught failure while replacing either file rolls back any file already replaced. Fault-injection coverage verifies both Review Checkpoint creation and Final Save updates.

Two distinct files cannot form a truly atomic filesystem transaction across an abrupt power loss or process termination between their two final replacements. If only one record exists, one remains `review_saved` while the other says `final`, or their Review content disagrees:

1. stop all further record writes;
2. keep both files unchanged;
3. report the mismatch and exact paths;
4. use the validated State files and the intact record, if any, only as evidence for a deliberate repair;
5. do not silently choose one record or regenerate over either one.

This is a documented Base v1 recovery boundary, not permission to add a database, background daemon, or cross-day reconstruction system.


## 6.0：最终存档后的可选团队空间同步

本地保存与校验先完成，再按 [team-space-sync.md](team-space-sync.md) 判断同步。`checkpoint --no-discussion` 直接形成 final、`finalize` 后形成 final，以及当天已 final 后追加支持并保存，均覆盖；review_saved、草稿及未完成复盘的暂停不上传为最终记录。同步失败不回滚本地文件，不改写原始 Review 或学习者原文。恢复后仍以经过正式校验的双记录生成内容快照，不从 HTML 或聊天重构。

确认非 WorkBuddy 时只存本地；成功完整查询后零匹配静默跳过，一个匹配自动处理，多个匹配询问选择。查询失败、无权限与上传失败不伪装成零匹配。保存命令输出的同步交接提示是下一步操作提示，不是已经上传的证据。具体网络调用由宿主按实际可用的资料库工具完成。


## 6.0-RC2：Windows 已保存页面但未归档的恢复

RC1 直接 import fcntl，在 Windows 首次归档前失败。RC2 已为正式存档、支持保存和同步队列提供 Windows 原生锁；不能仅凭“Windows 尚待验收”就拒绝尝试正式 helper，不取消并发保护，也不手工写一份伪正式记录。

恢复时使用用户原先授权的整个学习工作区。先运行正常 ensure、检查原 Assessment 与 Daily Page State 的实际日期、Task ID、保存状态及复盘必填数据；不从聊天数字或截图重新拼 State，不生成新的评估或计划页面覆盖旧数据。用户报告的“两张卷子各55分钟”不是替代原始 State 的依据。

- 如果存在 `.daily-srl/support-events/<原学习日期>.pending.json`，先使用 `support_events.py --workspace <root> --date <原学习日期> recover` 完成正式事务恢复，再 verify。不要删除 intent 或锁文件。
- 如果正式双记录已存在，先 verify；final 已完成就不重建 checkpoint，review_saved 则按原讨论阶段继续。
- 如果两份正式记录都不存在，且保存的 State 完整、日期一致、复盘已完成，在用户明确要求继续归档后，按实际讨论意愿运行原 `review_records.py ... checkpoint`。无进一步讨论时使用 `--no-discussion`；仍有讨论时使用 `--needs-discussion`，之后按需 finalize。日期来自原 State，不因当前系统日期改变而改写历史日期。
- 如果只有一份记录而无可用事务 intent，或 State 缺失、损坏、日期／任务不符，保留现场并准确说明；不能凭印象补造。

本地正式归档并校验后，再按团队空间规则尝试同步。页面保存、本地归档和远端同步分别报告结果。更新技能本身不应移动、清空学习目录，也不保证已经被覆盖的 State 能恢复。


## RC7: two-page connection failure and client upgrade

Apply the [shared page-save recovery contract](assessment-persistence.md#rc7-shared-save-recovery-assessment-and-daily-page) to both Assessment and Daily Page. First preserve current browser inputs; do not default to closing/reloading or asking the learner to refill. Browser drafts may help in the same browser origin, but they are not validated State and do not establish stage progression. Cancellation or a lost response means the result is unknown until checked. A successful initial readiness check is not proof a service remains alive minutes later.

After installation, close old clients only after protecting their unsaved inputs. Use the formal stop helper for an identified incompatible old service, then the normal page entry; its HTML-only client upgrade preserves State bytes and record files. Keep the original workspace and date. The new HTTP protocol refuses old-client unconditional writes; do not weaken this protection or patch an installed helper. Mac process checks, Windows branch simulations, browser fixtures and actual Windows/Mac WorkBuddy acceptance are separate evidence.
