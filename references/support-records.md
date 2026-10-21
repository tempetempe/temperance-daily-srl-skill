# 当日支持：集中整理与双记录（5.0）

## 保存时机：正常对话不逐轮写入

正常连续支持期间，不每条消息生成事件文件、保存并校验。在本次收尾统一整理；明确暂停、离开或需要切换对话时，集中保存一次必要事实。单次情绪表达、方法提议或分支变化本身不是保存触发器。不要求儿童填写第三张表，也不为后台写入反复索要确认。

Review 已完整提交且仍需讨论时，照常先保存原始 Review checkpoint，未整理的支持到讨论收尾再合并。无进一步讨论而直接结束时，用 checkpoint 的可选支持批次一并保存。页面自动保存和正式 Review 必填校验不变。

只有已完成并核验的写入算已保存。突然关闭可能让部分事实仅在宿主聊天里、尚未进入 Skill 记录；不承诺每句话持久化。不扫描宿主日志或旧日期补故事，不假定跨窗口拥有未保存对话。必要时只澄清当前继续所需的信息。

## 命令：一次操作内保存并校验

在授权 Workspace 的 `.daily-srl/runtime/` 准备一个批次文件，仅到保存节点才生成：

```sh
# Review 尚未完成时的明确暂停；或当日已结束后又有支持并再次收尾
python3 <skill>/scripts/support_events.py --workspace <root> --date YYYY-MM-DD save-batch --batch-file <batch.json>

# 完整 Review 后直接收尾
python3 <skill>/scripts/review_records.py --workspace <root> checkpoint --no-discussion --support-file <batch.json>

# 已有 review_saved，进一步讨论收尾；有实际新增讨论内容
python3 <skill>/scripts/review_records.py --workspace <root> finalize --discussion-file <discussion.json> --support-file <batch.json>
```

没有讨论新增内容时，使用 `finalize --no-additions --support-file <batch.json>`，不制作空讨论文件；不能用它省略已形成但未保存的讨论结果。没有新增支持则省略 `--support-file`。不要先 save-batch 再 finalize 同一批内容；finalizer 可以在一个事务中保存全部。成功命令内已检查实际输出及双记录一致性，不例行追加 verify/read 命令拖长收尾。恢复或诊断时才独立使用 show / verify：

```sh
python3 <skill>/scripts/support_events.py --workspace <root> --date YYYY-MM-DD show
python3 <skill>/scripts/support_events.py --workspace <root> --date YYYY-MM-DD verify
python3 <skill>/scripts/support_events.py --workspace <root> --date YYYY-MM-DD recover
```

旧 `save --event-file` 只为兼容旧调用，不用于 5.0 正常交流。正式事实源仍是 `.daily-srl/support-events/YYYY-MM-DD.json`；不保存到安装包或跨日偏好文件。

## 批次数据合同

新输入严格包含 schema_version、events、summaries。RC5 允许下述确定性默认值简写，落盘仍为原完整 schema，不削减事实字段。事实按实际顺序输入，不按编造的时间排序。下面示例仅在相应交流实际发生时成立：

```json
{
  "schema_version": 2,
  "events": [
    {
      "event_id": "support-001",
      "episode_id": "episode-001",
      "occurred_at": null,
      "time_source": "unknown",
      "time_evidence": "",
      "phase": "planning",
      "kind": "proposal",
      "branch": "emotional_support",
      "related_task_ids": [],
      "related_event_id": null,
      "strategy_ids": ["ER-03"],
      "learner_words": "我不想讲细节，有什么办法吗？",
      "agent_action": "尊重不讲细节的选择，提议短暂看看窗外，等待接受。",
      "learner_feedback": "",
      "learner_visible": ["学习者不想讲细节，Agent 提议短暂看看窗外。"]
    }
  ],
  "summaries": [
    {
      "summary_id": "summary-001",
      "episode_id": "episode-001",
      "title": "不讲细节时的支持",
      "paragraphs": ["学习者不想讲事件细节，希望获得支持。Agent 没有继续追问，提议短暂看看窗外；尚未收到是否接受、尝试或有何变化的反馈。"],
      "event_ids": ["support-001"],
      "supersedes": null
    }
  ]
}
```

### RC5 简写输入

在上述 schema_version=2 批次中，可同时省略事件的 `occurred_at` / `time_source` / `time_evidence` 三项，程序补为 `null` / `unknown` / `""`。三项只省略一部分会拒绝；有真实活动时间时仍提供完整三项和来源依据。显式非法值不会被默认值修正。

没有实际关联时，还可分别省略 `related_task_ids`、`strategy_ids`、`related_event_id`，分别补 `[]`、`[]`、`null`；首次摘要可省略 `supersedes`，补 `null`。有实际关联或修订关系时必须提供，不为少写字段而丢失事实。其它语义、原话、支持动作、反馈、learner_visible、摘要证据与 ID 字段仍按原合同提供。ID 由 Agent 明确稳定给出，重试复用，程序不自动生成新 ID。

简写仅减少固定工程值。未反馈仍是未反馈，不能填成改善、拒绝或有效。程序规范化后继续执行原校验、事务、去重和完整存储；仍使用相同的 `save-batch` / `--support-file`，不增加学习者表格或额外命令。

### 事实字段与来源

- event_id / episode_id：1–80 位字母、数字、下划线或连字符。同一段连续支持共用 episode_id；不按每条消息建一段支持。
- phase：entry / planning / coaching / execution / review / further_discussion / after_review。它说明发生阶段，不证明该阶段已完成。
- kind：preference / acknowledgement / proposal / decision / attempt / feedback / transition / correction。提出、接受、实际尝试、反馈不互相代替；可在同一事件用短句如实交代连续发生的事实。
- branch：undecided / learning / emotional_support。选择带着情绪先安排学习也可据实保留；不把普通礼貌都当情绪支持。
- related_task_ids：可空；有关联时必须来自当日已保存 Assessment 或 Review。不编 Task ID，不把之前无关联的事件自动追认为后来任务的支持。
- related_event_id：可空，或引用同段此前事件，说明接受、反馈、更正等针对哪个事实。
- learner_words / learner_feedback：只留必要原话，保持说话人、否定和不确定性，不将 Agent 的话归给学习者。没有则空，不留无关私人细节。
- agent_action：实际已提供的支持和功能操作，不保存隐藏思考、自我评价、诊断或“我已经正确遵守规则”的说明。
- strategy_ids：空或实际提出／使用的 ER-01 至 ER-12；编号不能证明方法已接受或已尝试。
- learner_visible：简短事实片段，便于旧格式兼容，不是现场整段回复。新格式最终儿童章节以 summaries 为准。
- 每项关键事实须能回到当前原话或已保存数据核对。以这些必要原话作为轻量依据，不新增儿童确认表或强制宿主私有消息 ID。脚本不会判断原话是否真正支持总结。

### 时间

新事件的规范化完整格式包含 time_source、time_evidence 和 occurred_at；输入可将未知时间三项一起省略，由程序补 unknown、空证据和 null。已知事件时间仅接受 learner_report 或 activity_timer，并写出对应活动的时间依据；occurred_at 需包含时区。不能以聊天间隔、保存时间、模型生成或工具等待推算活动时长。模拟学习日不自动确定事件的具体时刻。

保存程序为新事件和摘要生成 recorded_at（真实系统时间），Agent 不提供此字段。它不是活动发生时间，也不用于向孩子计算“情绪花了多少分钟”。不确定实际经过多久时，只提示原安排可能需调整。

### 收尾摘要与修订

- 每段支持的最新摘要用简短自然中文描述已经发生的过程；title 单行，paragraphs 非空。不是逐轮转述，也不评价支持效果或生成成长意义。
- event_ids 按留存顺序引用这段支持的全部事实。引用齐全不代表内容真实，Agent 仍须核对每个断言。
- 不复制“愿意试一下吗”等未结束的现场问句；不把未反馈写成拒绝、尝试或有效。
- 已有暂停摘要，收尾新增事实时，追加新摘要 ID，supersedes 指向同段当前摘要 ID，并引用该段全部事实；旧事实和旧摘要保留，儿童只看最新摘要。
- 更正事实另建 correction 事件并指向原事实；新摘要清楚体现最新澄清，历史留在内部，不偷偷改旧事件。
- 同一个 ID 不同内容会被拒绝；重试使用同一输入文件，不重新生成 ID、措辞或时间。已储存的 recorded_at 由程序管理，不复制进输入。
- 同日多段支持在一个“情绪方面的支持”章节内分别整理。无支持则没有该章节。

## 双记录与版本

支持日志 v2 为 `{schema_version: 2, record_date, events, summaries}`；读取兼容 v1。现有 v1 事件不重编时间或来源；只在当前授权日期需要更新时保留旧事实并受控升级，不遍历历史。

Structured Record 根 schema_version 仍为 1，新增 render_version = 2 和完整性校验值；可选 emotional_support 与日志同源，包含 events、summaries 及相应版本。review_checkpoint 始终不可改写。两个页面 State 与 Workspace layout 版本不变。

Learning Record 最终顺序为任务记录 → 今日小记 → 进一步聊到的内容（实际有内容时）→ 情绪方面的支持（实际发生时）。支持不是小记的一部分，不往反思／评价字段重复塞入。正常收尾仅向儿童提供易读记录入口，不展示内部路径、状态或策略编号。

5.0 保护原始事实与学习者文字，不再要求整个旧 Markdown 是新版文件的字节前缀。程序从记录数据重排生成部分、更新完成提示；原 Review、Task Note、Diary 和已有讨论数据不因排版变化被改写。

## 安全更新与恢复

写入前核对日志、双记录投影、版本和生成内容。v2 使用记录内容校验值与完整渲染比较；v1 只接受可完整重建的已知旧布局。发现独立编辑、损坏、版本未知或不一致即停止，不把它猜成可忽略差异。

每次更新前将待更新文件的旧内容保存到 `.daily-srl/record-backups/YYYY-MM-DD/<hash>.json`，内容寻址，不覆盖已有备份。备份仅用于当前记录恢复，不参与学习或跨日策略；不得自行清理。需要恢复手工修改时先报告差异，不能直接拿备份覆盖。

同日非阻塞文件锁串行化正式写入；准备后发现内容改变会停止。批量写入使用包含校验值的 pending 意图和失败回滚，普通写入失败及进程中断有测试覆盖，但不是跨文件硬件级原子事务，也不能消除不遵守锁的外部编辑竞争。

存在有效 pending 时，普通写入被阻止；recover 仅完成目标内容仍匹配原前／后状态的事务，然后 verify。意图损坏、外部编辑或无意图的不一致均停下保留诊断，不删除意图、不手工拼记录。仅有支持而 Review 未完成时不生成假正式记录；final 后新支持可在再次收尾时更新支持节，不重开 Review。

verify 检查工程完整性而非事实真实性。旧记录没有 emotional_support 表示没有相应留存，不证明没有发生支持。


## Final local save → optional team-space sync

已 final 的记录在本次支持保存后仍为 final 时，按 [team-space-sync.md](team-space-sync.md) 处理新的已保存内容。未完成 Review 的支持暂停不上传为正式最终记录；保存提示不是云端成功证据。同步失败不影响已完成的本地保存。
