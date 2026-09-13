#!/usr/bin/env python3
"""Generate the formal Daily SRL Daily Page from saved Assessment state."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from persistence_bridge import validate_assessment_state, validate_daily_page_state
from workspace_runtime import (
    WorkspaceRuntimeError,
    atomic_write_text,
    ensure_workspace,
    inspect_pair,
    recover_pair,
    render_html as render_runtime_html,
    write_runtime_pair,
)

CONFIDENCE_TO_VISUAL = {
    "confident": "normal",
    "uncertain": "notice",
    "not_confident": "focus",
}
COACHING_FIELDS = (
    "main_obstacles",
    "actions",
    "resources",
    "help_or_communication",
    "other_confirmed_information",
)
PLAN_FIELDS = {"task", "planned_start", "estimated_duration_minutes"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def text_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field}[{index}] must be a non-empty string")
        result.append(item.strip())
    return result


def normalize_coaching(value: Any, task_id: str) -> dict[str, list[str]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"coaching for {task_id} must be an object")
    unknown = set(value) - set(COACHING_FIELDS)
    if unknown:
        raise ValueError(f"coaching for {task_id} has unsupported fields: {sorted(unknown)}")
    result = {field: text_list(value.get(field), f"{task_id}.{field}") for field in COACHING_FIELDS}
    return result if any(result.values()) else None


def normalize_plan_override(value: Any, task_id: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"final_plan for {task_id} must be an object")
    unknown = set(value) - PLAN_FIELDS
    if unknown:
        raise ValueError(f"final_plan for {task_id} has unsupported fields: {sorted(unknown)}")
    result: dict[str, Any] = {}
    for field in ("task", "planned_start"):
        if field in value:
            item = value[field]
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{task_id}.final_plan.{field} must be a non-empty string")
            result[field] = item.strip()
    if "estimated_duration_minutes" in value:
        duration = value["estimated_duration_minutes"]
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            raise ValueError(
                f"{task_id}.final_plan.estimated_duration_minutes must be a positive integer"
            )
        result["estimated_duration_minutes"] = duration
    return result


def build_state(assessment: Any, coaching_handoff: Any) -> dict[str, Any]:
    if not isinstance(assessment, dict) or assessment.get("schema_version") != 1:
        raise ValueError("Assessment state is invalid")
    page_date = date.fromisoformat(str(assessment.get("assessment_date"))).isoformat()
    raw_tasks = assessment.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("Assessment tasks must be an array")
    if coaching_handoff is None:
        coaching_handoff = {}
    if not isinstance(coaching_handoff, dict):
        raise ValueError("Coaching handoff must be an object keyed by task ID")

    assessment_ids = {item.get("task_id") for item in raw_tasks if isinstance(item, dict)}
    unknown_ids = set(coaching_handoff) - assessment_ids
    if unknown_ids:
        raise ValueError(f"Coaching handoff contains unknown task IDs: {sorted(unknown_ids)}")

    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(raw_tasks):
        if not isinstance(item, dict):
            raise ValueError(f"Assessment task {index} must be an object")
        task_id = item.get("task_id")
        task_text = item.get("task")
        planned_start = item.get("planned_start")
        duration = item.get("estimated_duration_minutes")
        confidence = item.get("confidence")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"Assessment task {index} has no task ID")
        if not isinstance(task_text, str) or not task_text.strip():
            raise ValueError(f"Assessment task {task_id} has no task name")
        if not isinstance(planned_start, str) or not planned_start.strip():
            raise ValueError(f"Assessment task {task_id} has no planned start")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            raise ValueError(f"Assessment task {task_id} has invalid duration")
        if confidence not in CONFIDENCE_TO_VISUAL:
            raise ValueError(f"Assessment task {task_id} has invalid confidence")

        handoff = coaching_handoff.get(task_id)
        if handoff is not None and not isinstance(handoff, dict):
            raise ValueError(f"Coaching handoff for {task_id} must be an object")
        if handoff is not None:
            unknown_handoff_fields = set(handoff) - {"coaching_result", "final_plan"}
            if unknown_handoff_fields:
                raise ValueError(
                    f"Coaching handoff for {task_id} has unsupported fields: {sorted(unknown_handoff_fields)}"
                )
        coaching_result = normalize_coaching(
            handoff.get("coaching_result") if handoff else None, task_id
        )
        plan_override = normalize_plan_override(handoff.get("final_plan") if handoff else None, task_id)
        final_plan = {
            "task": plan_override.get("task", task_text.strip()),
            "planned_start": plan_override.get("planned_start", planned_start.strip()),
            "estimated_duration_minutes": plan_override.get("estimated_duration_minutes", duration),
        }
        tasks.append(
            {
                "task_id": task_id,
                "source_confidence": confidence,
                "visual_state": CONFIDENCE_TO_VISUAL[confidence],
                "coaching_result": coaching_result,
                "final_plan": final_plan,
                "review": {
                    "completion": "",
                    "completion_details": "",
                    "actual_duration_minutes": None,
                    "expectation": "",
                    "task_note": "",
                },
            }
        )

    return {
        "schema_version": 2,
        "page_date": page_date,
        "generated_at": utc_now(),
        "saved_at": None,
        "tasks": tasks,
        "daily_diary": "",
    }


def render_html(state: dict[str, Any]) -> str:
    return render_runtime_html("daily-page", state)


def refresh_html(workspace: Path) -> Path:
    """Refresh only the runtime HTML while preserving the authoritative page state."""
    paths = ensure_workspace(workspace)
    recovered = recover_pair(
        paths.workspace,
        "daily-page",
        lambda value: validate_daily_page_state(value, mark_saved=False),
    )
    if recovered["status"] == "absent":
        raise FileNotFoundError("Daily Page Runtime does not exist")
    state = recovered["state"]
    html_path = paths.runtime / "daily-page.html"
    atomic_write_text(html_path, render_html(state))
    return html_path


def generate(
    workspace: Path,
    coaching_file: Path | None = None,
    force: bool = False,
) -> tuple[Path, Path]:
    paths = ensure_workspace(workspace)
    workspace = paths.workspace
    assessment_recovery = recover_pair(
        workspace,
        "assessment",
        lambda value: validate_assessment_state(value, mark_saved=False),
    )
    if assessment_recovery["status"] == "absent":
        raise FileNotFoundError("Assessment Runtime does not exist")
    assessment_path = paths.runtime / "assessment-state.json"
    assessment = load_json(assessment_path)
    coaching = load_json(coaching_file) if coaching_file else {}
    state = build_state(assessment, coaching)

    daily_pair = inspect_pair(workspace, "daily-page")
    if daily_pair["status"] not in {"absent", "ready"}:
        recover_pair(
            workspace,
            "daily-page",
            lambda value: validate_daily_page_state(value, mark_saved=False),
        )
        daily_pair = inspect_pair(workspace, "daily-page")
    if daily_pair["status"] == "ready":
        previous = validate_daily_page_state(
            load_json(daily_pair["state_path"]), mark_saved=False
        )
        if previous["page_date"] == state["page_date"] and not force:
            raise RuntimeError(
                f"Daily Page runtime already exists for {state['page_date']}; use --force to replace it intentionally"
            )

    return write_runtime_pair(workspace, "daily-page", state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--coaching-file", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--refresh-html-only",
        action="store_true",
        help="refresh runtime HTML from the current state without modifying state",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.refresh_html_only:
            html_path = refresh_html(args.workspace)
            print(f"Refreshed {html_path}")
            return 0
        html_path, state_path = generate(
            args.workspace, args.coaching_file, args.force
        )
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
        RuntimeError,
        WorkspaceRuntimeError,
    ) as error:
        raise SystemExit(f"error: {error}") from error
    print(f"Generated {html_path}")
    print(f"Initialized {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
