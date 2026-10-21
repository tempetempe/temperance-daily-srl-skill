#!/usr/bin/env python3
"""Preserve Review facts and render versioned, consolidated learner records."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from persistence_bridge import validate_assessment_state, validate_daily_page_state  # noqa: E402
import support_events as support  # noqa: E402
from workspace_runtime import (  # noqa: E402
    WorkspaceRuntimeError,
    _assert_authorized_workspace,
    ensure_directory,
    ensure_workspace,
    read_workspace_marker,
    workspace_paths,
)


STRUCTURED_SCHEMA_VERSION = 1
DISCUSSION_FIELDS = (
    "observations",
    "hypotheses",
    "confirmed_conclusions",
    "positive_evidence",
    "findings",
    "experience_or_lesson",
    "adaptation",
    "resource_adjustment",
    "possible_follow_up",
)
STATUS_PATTERN = re.compile(r"<!-- daily-srl-record-status: (review_saved|final) -->")

CONFIDENCE_LABELS = {
    "confident": "有把握",
    "uncertain": "有点悬",
    "not_confident": "没把握",
}
COMPLETION_LABELS = {
    "completed": "已完成",
    "partial": "部分完成",
    "not_completed": "未完成",
    "changed": "实际安排发生变化",
}
EXPECTATION_LABELS = {
    "better": "比预想更好",
    "as_expected": "和预想差不多",
    "below_expected": "没有达到预期",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def require_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    return result


def require_text_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return [require_string(item, f"{field}[{index}]") for index, item in enumerate(value)]


def stage_text(path: Path, content: str) -> Path:
    ensure_directory(path.parent)
    handle, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=f".{uuid.uuid4().hex}.tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def replace_pair(outputs: dict[Path, str]) -> None:
    """Stage both records before replacing either and roll back ordinary write failures."""
    originals = {path: path.read_bytes() if path.exists() else None for path in outputs}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, content in outputs.items():
            staged[path] = stage_text(path, content)
        for path in outputs:
            os.replace(staged[path], path)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                rollback = stage_text(path, original.decode("utf-8"))
                os.replace(rollback, path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def state_paths(workspace: Path) -> tuple[Path, Path, Path, Path]:
    paths = ensure_workspace(workspace)
    return (
        paths.runtime / "assessment-state.json",
        paths.runtime / "daily-page-state.json",
        paths.structured_records,
        paths.learning_records,
    )


def load_review_states(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    assessment_path, daily_path, _, _ = state_paths(workspace)
    assessment = validate_assessment_state(load_json(assessment_path), mark_saved=False)
    daily = validate_daily_page_state(load_json(daily_path), mark_saved=False)
    if assessment["assessment_date"] != daily["page_date"]:
        raise ValueError("Assessment and Daily Page dates do not match")
    assessment_ids = [task["task_id"] for task in assessment["tasks"]]
    daily_ids = [task["task_id"] for task in daily["tasks"]]
    if assessment_ids != daily_ids:
        raise ValueError("Assessment and Daily Page task IDs or order do not match")
    if assessment["saved_at"] is None:
        raise ValueError("Assessment has not been saved")
    if daily["saved_at"] is None:
        raise ValueError("Daily Page has not been saved")
    for task in daily["tasks"]:
        label = task["final_plan"]["task"]
        review = task["review"]
        if not review["completion"]:
            raise ValueError(f"Review completion is missing for {label}")
        if review["actual_duration_minutes"] is None:
            raise ValueError(f"Actual duration is missing for {label}")
        if not review["expectation"]:
            raise ValueError(f"Review expectation is missing for {label}")
    return assessment, daily


def inspect_review(workspace: Path) -> dict[str, Any]:
    """Read validated learning facts without initializing or changing the workspace.

    Readiness is a view of these source snapshots, never authorization to bypass
    the current lock-protected checks in checkpoint/finalize. Learner text is
    selected directly from the validated raw inputs, without summarizing or
    replacing it with the validators' whitespace-normalized values.
    """
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "missing",
        "ready": False,
        "checks": {},
        "missing": [],
        "errors": [],
        "facts": None,
    }
    try:
        paths = workspace_paths(_assert_authorized_workspace(workspace))
        targets = {
            "assessment": paths.runtime / "assessment-state.json",
            "daily_page": paths.runtime / "daily-page-state.json",
        }
        for path in (paths.root, paths.runtime, paths.marker, *targets.values()):
            if path.is_symlink():
                raise WorkspaceRuntimeError(f"refusing symlink: {path}")
        for directory in (paths.root, paths.runtime):
            if directory.exists() and not directory.is_dir():
                raise WorkspaceRuntimeError(f"Daily SRL path is not a directory: {directory}")
        if paths.marker.exists():
            read_workspace_marker(paths.workspace)
        if paths.migration_pending.exists():
            raise WorkspaceRuntimeError("incomplete migration preserved; use the formal migrate-layout helper")
    except (OSError, ValueError, TypeError, KeyError, WorkspaceRuntimeError) as error:
        result["status"] = "invalid"
        result["errors"].append({"source": "workspace", "message": str(error)})
        return result

    raw, states = {}, {}
    validators = {"assessment": validate_assessment_state, "daily_page": validate_daily_page_state}
    for source, path in targets.items():
        if not path.exists():
            result["checks"][f"{source}_state"] = "missing"
            result["missing"].append({"source": source, "field": "state"})
            continue
        try:
            raw[source] = load_json(path)
            states[source] = validators[source](raw[source], mark_saved=False)
        except (OSError, ValueError, TypeError, KeyError) as error:
            result["checks"][f"{source}_state"] = "invalid"
            result["errors"].append({"source": source, "message": str(error)})
        else:
            result["checks"][f"{source}_state"] = "valid"
    if result["errors"]:
        result["status"] = "invalid"
        return result
    if len(states) != 2:
        return result

    assessment, daily = states["assessment"], states["daily_page"]
    checks = result["checks"]
    checks["dates_match"] = assessment["assessment_date"] == daily["page_date"]
    checks["task_ids_and_order_match"] = (
        [task["task_id"] for task in assessment["tasks"]]
        == [task["task_id"] for task in daily["tasks"]]
    )
    for check, message in (("dates_match", "Assessment and Daily Page dates do not match"),
                           ("task_ids_and_order_match", "Assessment and Daily Page task IDs or order do not match")):
        if not checks[check]:
            result["errors"].append({"source": "pair", "message": message})
    if result["errors"]:
        result["status"] = "invalid"
        return result

    for source, state in states.items():
        checks[f"{source}_saved"] = state["saved_at"] is not None
        if not checks[f"{source}_saved"]:
            result["missing"].append({"source": source, "field": "saved_at"})
    for task in daily["tasks"]:
        for field in ("completion", "actual_duration_minutes", "expectation"):
            value = task["review"][field]
            if value is None or value == "":
                result["missing"].append({"source": "daily_page", "task_id": task["task_id"],
                                          "field": f"review.{field}"})
    facts = []
    for original, task, validated in zip(raw["assessment"]["tasks"], raw["daily_page"]["tasks"], daily["tasks"]):
        review = task["review"]
        facts.append({
            "task_id": validated["task_id"],
            "assessment": {key: original.get(key) for key in (
                "task", "planned_start", "estimated_duration_minutes", "confidence")},
            "coaching_result": task.get("coaching_result"),
            "final_plan": task["final_plan"],
            "actual_performance": {key: review.get(key) for key in (
                "completion", "completion_details", "actual_duration_minutes")},
            "review": {"expectation": review["expectation"]},
            "task_note": review["task_note"],
        })
    result["facts"] = {"record_date": daily["page_date"], "tasks": facts,
                       "daily_diary": raw["daily_page"]["daily_diary"]}
    result["ready"] = not result["missing"]
    result["status"] = "ready" if result["ready"] else "incomplete"
    return result


def build_checkpoint(
    assessment: dict[str, Any], daily: dict[str, Any], *, needs_discussion: bool, saved_at: str
) -> dict[str, Any]:
    assessment_by_id = {task["task_id"]: task for task in assessment["tasks"]}
    tasks: list[dict[str, Any]] = []
    for daily_task in daily["tasks"]:
        task_id = daily_task["task_id"]
        original = assessment_by_id[task_id]
        review = daily_task["review"]
        tasks.append(
            {
                "task_id": task_id,
                "assessment": {
                    "task": original["task"],
                    "planned_start": original["planned_start"],
                    "estimated_duration_minutes": original["estimated_duration_minutes"],
                    "confidence": original["confidence"],
                },
                "coaching_result": daily_task["coaching_result"],
                "final_plan": daily_task["final_plan"],
                "actual_performance": {
                    "completion": review["completion"],
                    "completion_details": review["completion_details"],
                    "actual_duration_minutes": review["actual_duration_minutes"],
                },
                "review": {"expectation": review["expectation"]},
                "task_note": review["task_note"],
            }
        )
    return {
        "schema_version": STRUCTURED_SCHEMA_VERSION,
        "record_date": daily["page_date"],
        "record_status": "review_saved" if needs_discussion else "final",
        "review_checkpoint": {
            "saved_at": saved_at,
            "source_state": {
                "assessment": {
                    "schema_version": assessment["schema_version"],
                    "generated_at": assessment["generated_at"],
                    "saved_at": assessment["saved_at"],
                },
                "daily_page": {
                    "schema_version": daily["schema_version"],
                    "generated_at": daily["generated_at"],
                    "saved_at": daily["saved_at"],
                },
            },
            "tasks": tasks,
            "daily_diary": daily["daily_diary"],
        },
    }


def quote_text(value: str) -> str:
    if not value.strip():
        return "_未填写_"
    return "\n".join(f"> {line}" if line else ">" for line in value.splitlines())


def bullet_list(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "_无_"


def render_coaching(value: dict[str, list[str]] | None) -> str:
    if value is None:
        return "_这项任务没有需要补充的学习安排。_"
    sections = [
        ("目前最卡的地方", "main_obstacles"),
        ("我们商量好的应对方式", "actions"),
        ("手上能用的资源", "resources"),
        ("需要找谁帮忙，或要沟通什么", "help_or_communication"),
        ("还有这些需要记住", "other_confirmed_information"),
    ]
    visible = [(label, field) for label, field in sections if value.get(field)]
    if not visible:
        return "_本任务没有需要保留的额外安排。_"
    return "\n\n".join(
        f"#### {label}\n\n{bullet_list(value[field])}" for label, field in visible
    )


def render_learning_record(record: dict[str, Any]) -> str:
    checkpoint = record["review_checkpoint"]
    status = record["record_status"]
    status_text = (
        "截至今日复盘的内容已经保存；进一步讨论确认后会追加在文件末尾。"
        if status == "review_saved"
        else "今日复盘已经完成并保存。"
    )
    parts = [
        "<!-- daily-srl-learning-record: 1 -->",
        f"# {record['record_date']} 学习记录",
        f"> {status_text}",
    ]
    for index, task in enumerate(checkpoint["tasks"], start=1):
        assessment = task["assessment"]
        plan = task["final_plan"]
        actual = task["actual_performance"]
        review = task["review"]
        parts.extend(
            [
                f"## 任务 {index}｜{plan['task']}",
                "### 最初的安排与判断",
                "\n".join(
                    [
                        f"- 任务：{assessment['task']}",
                        f"- 准备时间：{assessment['planned_start']}",
                        f"- 预计用时：{assessment['estimated_duration_minutes']} 分钟",
                        f"- 当时的把握：{CONFIDENCE_LABELS[assessment['confidence']]}",
                    ]
                ),
                "### 关于这项任务",
                render_coaching(task["coaching_result"]),
                "### 最终安排",
                "\n".join(
                    [
                        f"- 任务：{plan['task']}",
                        f"- 准备时间：{plan['planned_start']}",
                        f"- 预计用时：{plan['estimated_duration_minutes']} 分钟",
                    ]
                ),
                "### 实际情况",
                "\n".join(
                    [
                        f"- 完成情况：{COMPLETION_LABELS[actual['completion']]}",
                        f"- 实际用时：{actual['actual_duration_minutes']} 分钟",
                    ]
                ),
            ]
        )
        if actual["completion_details"]:
            parts.extend(["#### 发生了什么？", quote_text(actual["completion_details"])])
        parts.extend(
            [
                "### 复盘",
                f"- 与原先预期相比：{EXPECTATION_LABELS[review['expectation']]}",
                "#### 关于这个任务，还有什么想记下来的？",
                quote_text(task["task_note"]),
            ]
        )
    parts.extend(
        [
            "## 今日小记",
            quote_text(checkpoint["daily_diary"]),
            f"<!-- daily-srl-record-status: {status} -->",
        ]
    )
    return "\n\n".join(parts) + "\n"


def checkpoint_records(workspace: Path, *, needs_discussion: bool, support_file: Path | None = None) -> tuple[Path, Path]:
    _, daily = load_review_states(workspace)
    with support.day_lock(workspace, daily["page_date"]):
        return _checkpoint_records(workspace, needs_discussion=needs_discussion, support_file=support_file)


def _checkpoint_records(workspace: Path, *, needs_discussion: bool, support_file: Path | None = None) -> tuple[Path, Path]:
    assessment, daily = load_review_states(workspace)
    _, _, structured_dir, learning_dir = state_paths(workspace)
    date_value = daily["page_date"]
    structured_path = structured_dir / f"{date_value}.json"
    learning_path = learning_dir / f"{date_value}.md"
    if structured_path.exists() or learning_path.exists():
        raise FileExistsError(
            "Review records already exist for this date; refusing to overwrite the checkpoint"
        )
    record = build_checkpoint(
        assessment, daily, needs_discussion=needs_discussion, saved_at=utc_now()
    )
    paths, targets = support.paths_for(workspace, date_value)
    before = {p: p.read_bytes() if p.exists() else None for p in targets}
    if before[structured_path] is not None or before[learning_path] is not None:
        raise FileExistsError("records appeared during checkpoint; refusing overwrite")
    journal = support.load_journal(workspace, date_value)
    if support_file is not None:
        journal = support.merge_batch(journal, load_json(support_file), {t["task_id"] for t in daily["tasks"]})
    events = journal["events"]
    valid_ids = {task["task_id"] for task in daily["tasks"]}
    if any(set(event["related_task_ids"]) - valid_ids for event in events):
        raise ValueError("Support events refer to tasks outside this Review; preserve for diagnosis")
    if events:
        record["emotional_support"] = support.journal_projection(journal)
    learning = seal_record(record)
    outputs = {structured_path: support.encode(record), learning_path: learning}
    if support_file is not None:
        outputs[targets[0]] = support.encode(journal)
    support.commit(workspace, date_value, outputs, expected_before=before)
    support.verify_unlocked(workspace, date_value)
    return structured_path, learning_path


def normalize_discussion(value: Any, valid_task_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Further Discussion result must be an object")
    unknown = set(value) - {"topics", "additional_notes"}
    if unknown:
        raise ValueError(f"Further Discussion result has unsupported fields: {sorted(unknown)}")
    raw_topics = value.get("topics")
    if not isinstance(raw_topics, list):
        raise ValueError("Further Discussion result must contain an explicit topics list")
    topics: list[dict[str, Any]] = []
    for index, raw_topic in enumerate(raw_topics):
        prefix = f"topics[{index}]"
        if not isinstance(raw_topic, dict):
            raise ValueError(f"{prefix} must be an object")
        allowed = {"topic", "related_task_ids", *DISCUSSION_FIELDS}
        topic_unknown = set(raw_topic) - allowed
        if topic_unknown:
            raise ValueError(f"{prefix} has unsupported fields: {sorted(topic_unknown)}")
        related_ids = require_text_list(raw_topic.get("related_task_ids"), f"{prefix}.related_task_ids")
        invalid_ids = set(related_ids) - valid_task_ids
        if invalid_ids:
            raise ValueError(f"{prefix} contains unknown task IDs: {sorted(invalid_ids)}")
        normalized: dict[str, Any] = {
            "topic": require_string(raw_topic.get("topic"), f"{prefix}.topic"),
            "related_task_ids": related_ids,
        }
        for field in DISCUSSION_FIELDS:
            items = require_text_list(raw_topic.get(field), f"{prefix}.{field}")
            if items:
                normalized[field] = items
        topics.append(normalized)
    return {
        "topics": topics,
        "additional_notes": require_text_list(value.get("additional_notes"), "additional_notes"),
    }


def last_learning_status(content: str) -> str | None:
    matches = STATUS_PATTERN.findall(content)
    return matches[-1] if matches else None


def render_discussion_append(
    discussion: dict[str, Any], *, task_names: dict[str, str], saved_at: str
) -> str:
    labels = {
        "observations": "今天的经历和感受",
        "hypotheses": "可能的原因，还不确定",
        "confirmed_conclusions": "我们弄清楚的事",
        "positive_evidence": "今天做得不错的地方",
        "findings": "新的认识",
        "experience_or_lesson": "经验或教训",
        "adaptation": "之后可以尝试的调整",
        "resource_adjustment": "资源使用上的调整",
        "possible_follow_up": "可能需要继续关注",
    }
    parts = ["## 进一步聊到的内容"]
    for topic in discussion["topics"]:
        parts.append(f"### {topic['topic']}")
        if topic["related_task_ids"]:
            names = [task_names[task_id] for task_id in topic["related_task_ids"]]
            parts.append(f"关联任务：{'、'.join(names)}")
        for field in DISCUSSION_FIELDS:
            if field in topic:
                parts.extend([f"#### {labels[field]}", bullet_list(topic[field])])
    if discussion["additional_notes"]:
        parts.extend(["### 其他想留下的内容", bullet_list(discussion["additional_notes"])])
    if not discussion["topics"] and not discussion["additional_notes"]:
        parts.append("本次没有补充内容。")
    parts.extend(
        [
            f"> 保存于：{saved_at}",
            "<!-- daily-srl-record-status: final -->",
        ]
    )
    return "\n\n".join(parts) + "\n"


def render_complete_record(record: dict[str, Any]) -> str:
    """The generated layout is replaceable; checkpoint values are not."""
    base = render_learning_record(record)
    marker = f"<!-- daily-srl-record-status: {record['record_status']} -->\n"
    parts = [base.removesuffix(marker).rstrip()]
    discussion = record.get("further_discussion")
    if discussion and (discussion["topics"] or discussion["additional_notes"]):
        names = {t["task_id"]: t["final_plan"]["task"] for t in record["review_checkpoint"]["tasks"]}
        rendered = render_discussion_append(discussion, task_names=names, saved_at=discussion["saved_at"])
        suffix = f"\n\n> 保存于：{discussion['saved_at']}\n\n<!-- daily-srl-record-status: final -->\n"
        parts.append(rendered.removesuffix(suffix))
    supported = support.render_support(record.get("emotional_support", {}))
    if supported:
        parts.append(supported.rstrip())
    parts.append(marker.rstrip())
    return "\n\n".join(parts) + "\n"


def record_content_hash(record):
    value = {k: v for k, v in record.items() if k != "integrity"}
    return support.digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def seal_record(record):
    record["render_version"] = 2
    learning = render_complete_record(record)
    record["integrity"] = {"record_sha256": record_content_hash(record),
                           "learning_sha256": support.digest(learning.encode("utf-8"))}
    return learning


def verify_render_integrity(record, learning):
    expected = {"record_sha256": record_content_hash(record),
                "learning_sha256": support.digest(learning.encode("utf-8"))}
    if record.get("integrity") != expected or render_complete_record(record) != learning:
        raise ValueError("record content changed or rendering mismatch; preserve user edits")


def legacy_render_matches(record, learning, events):
    """Only adopt exactly recognized v1 layouts; never infer edits away."""
    original = dict(record)
    discussion = record.get("further_discussion")
    if discussion:
        original["record_status"] = "review_saved"
    base = render_learning_record(original)
    fragments = [support.render_event(e) for e in events]
    if not discussion:
        return learning == base + "".join(fragments)
    names = {t["task_id"]: t["final_plan"]["task"] for t in record["review_checkpoint"]["tasks"]}
    addition = "\n" + render_discussion_append(discussion, task_names=names, saved_at=discussion["saved_at"])
    return any(learning == base + "".join(fragments[:n]) + addition + "".join(fragments[n:])
               for n in range(len(fragments) + 1))


def finalize_records(workspace: Path, discussion_file: Path | None = None, support_file: Path | None = None,
                     *, no_additions: bool = False) -> tuple[Path, Path]:
    if (discussion_file is None) == (not no_additions):
        raise ValueError("choose exactly one discussion_file or no_additions")
    _, daily_path, _, _ = state_paths(workspace)
    daily = validate_daily_page_state(load_json(daily_path), mark_saved=False)
    with support.day_lock(workspace, daily["page_date"]):
        return _finalize_records(workspace, discussion_file, support_file, no_additions=no_additions)


def _finalize_records(workspace: Path, discussion_file: Path | None, support_file: Path | None = None,
                      *, no_additions: bool = False) -> tuple[Path, Path]:
    discussion_raw = {"topics": [], "additional_notes": []} if no_additions else load_json(discussion_file)
    _, daily_path, structured_dir, learning_dir = state_paths(workspace)
    daily = validate_daily_page_state(load_json(daily_path), mark_saved=False)
    date_value = daily["page_date"]
    structured_path = structured_dir / f"{date_value}.json"
    learning_path = learning_dir / f"{date_value}.md"
    paths, targets = support.paths_for(workspace, date_value)
    before = {p: p.read_bytes() if p.exists() else None for p in targets}
    journal = support.load_journal(workspace, date_value)
    record = support.read_pair(targets, journal, date_value)
    if not isinstance(record, dict) or record.get("schema_version") != STRUCTURED_SCHEMA_VERSION:
        raise ValueError("Structured Record is invalid")
    if record.get("record_date") != date_value:
        raise ValueError("Structured Record date does not match Daily Page")
    if record.get("record_status") not in ("review_saved", "final"):
        raise ValueError("Structured Record is not waiting for Further Discussion")
    if "further_discussion" in record and record["record_status"] != "final":
        raise ValueError("Structured Record already contains Further Discussion")
    learning_before = learning_path.read_text(encoding="utf-8")
    checkpoint = record.get("review_checkpoint")
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("tasks"), list):
        raise ValueError("Structured Record Review checkpoint is invalid")
    task_names = {
        task["task_id"]: task["final_plan"]["task"] for task in checkpoint["tasks"]
    }
    discussion = normalize_discussion(discussion_raw, set(task_names))
    merged = support.merge_batch(journal, load_json(support_file), set(task_names)) if support_file else journal
    if record["record_status"] == "final":
        previous = record.get("further_discussion", {})
        if {k: v for k, v in previous.items() if k != "saved_at"} != discussion or merged != journal:
            raise ValueError("already final; only an identical finalization retry is allowed")
        support.verify_unlocked(workspace, date_value)
        return structured_path, learning_path
    saved_at = utc_now()
    final_record = dict(record)
    final_record["record_status"] = "final"
    final_record["further_discussion"] = {"saved_at": saved_at, **discussion}
    if merged["events"]:
        final_record["emotional_support"] = support.journal_projection(merged)
    learning_after = seal_record(final_record)
    outputs = {structured_path: support.encode(final_record), learning_path: learning_after}
    if support_file is not None:
        outputs[targets[0]] = support.encode(merged)
    support.commit(workspace, date_value, outputs, expected_before=before)
    support.verify_unlocked(workspace, date_value)
    return structured_path, learning_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    checkpoint = subparsers.add_parser("checkpoint", help="save both records through Review")
    checkpoint.add_argument("--support-file", type=Path, help="optional consolidated support batch at session closure")
    decision = checkpoint.add_mutually_exclusive_group(required=True)
    decision.add_argument("--needs-discussion", action="store_true")
    decision.add_argument("--no-discussion", action="store_true")

    finalize = subparsers.add_parser("finalize", help="append confirmed Further Discussion")
    discussion = finalize.add_mutually_exclusive_group(required=True)
    discussion.add_argument("--discussion-file", type=Path)
    discussion.add_argument("--no-additions", action="store_true", help="close with no new discussion content")
    finalize.add_argument("--support-file", type=Path, help="optional consolidated support batch in the same transaction")
    inspect = subparsers.add_parser("inspect", help="read validated facts/readiness without writing files")
    # Keep existing global argument order and allow the readable inspect form.
    inspect.add_argument("--workspace", type=Path, default=argparse.SUPPRESS)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.workspace is None:
        parser.error("--workspace is required")
    try:
        if args.command == "inspect":
            result = inspect_review(args.workspace)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["status"] == "invalid" else 0
        elif args.command == "checkpoint":
            paths = checkpoint_records(args.workspace, needs_discussion=args.needs_discussion, support_file=args.support_file)
        else:
            paths = finalize_records(args.workspace, args.discussion_file, args.support_file,
                                     no_additions=args.no_additions)
    except (
        FileNotFoundError,
        FileExistsError,
        json.JSONDecodeError,
        OSError,
        ValueError,
        WorkspaceRuntimeError,
    ) as error:
        raise SystemExit(f"error: {error}") from error
    print(f"Updated {paths[0]}")
    print(f"Updated {paths[1]}")
    # Local saving already succeeded. Sync guidance must never roll it back or
    # turn a successful save into a failed save when host tooling is unavailable.
    try:
        from team_space_sync import post_save_handoff
        record_date = json.loads(paths[0].read_text(encoding="utf-8"))["record_date"]
        print("Team sync handoff: " + json.dumps(post_save_handoff(args.workspace, record_date), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print(f"Local records saved; sync handoff unavailable: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
