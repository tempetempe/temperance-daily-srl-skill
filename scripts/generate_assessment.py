#!/usr/bin/env python3
"""Generate the formal Daily SRL Assessment runtime for one local date."""

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

from workspace_runtime import (
    WorkspaceRuntimeError,
    ensure_workspace,
    inspect_pair,
    write_runtime_pair,
)
from persistence_bridge import validate_assessment_state, validate_daily_page_state


CONFIDENCE_MAP = {
    "": "",
    "有把握": "confident",
    "有点悬": "uncertain",
    "没把握": "not_confident",
    "confident": "confident",
    "uncertain": "uncertain",
    "not_confident": "not_confident",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_date(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def load_prefill_file(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("--tasks-file must contain a JSON array of objects")
    return payload


def build_state(assessment_date: str, prefill: list[dict[str, Any]]) -> dict[str, Any]:
    date_part = assessment_date.replace("-", "")
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(prefill, start=1):
        task_text = str(item.get("task", "")).strip()
        if not task_text:
            raise ValueError(f"prefill task {index} has an empty task name")

        raw_duration = item.get("estimated_duration_minutes")
        if raw_duration in (None, ""):
            duration = None
        elif isinstance(raw_duration, bool):
            raise ValueError(f"prefill task {index} has an invalid duration")
        else:
            duration = int(raw_duration)
            if duration < 1:
                raise ValueError(f"prefill task {index} duration must be at least 1 minute")

        raw_confidence = str(item.get("confidence", ""))
        if raw_confidence not in CONFIDENCE_MAP:
            raise ValueError(f"prefill task {index} has an unsupported confidence value")

        tasks.append(
            {
                "task_id": f"task-{date_part}-{index:03d}",
                "task": task_text,
                "planned_start": str(item.get("planned_start", "")),
                "estimated_duration_minutes": duration,
                "confidence": CONFIDENCE_MAP[raw_confidence],
            }
        )

    return {
        "schema_version": 1,
        "assessment_date": assessment_date,
        "generated_at": utc_now(),
        "saved_at": None,
        "next_task_number": len(tasks) + 1,
        "tasks": tasks,
    }


def generate(
    workspace: Path,
    assessment_date: str,
    prefill: list[dict[str, Any]],
    force: bool = False,
) -> tuple[Path, Path]:
    paths = ensure_workspace(workspace)
    workspace = paths.workspace
    assessment_pair = inspect_pair(workspace, "assessment")
    daily_pair = inspect_pair(workspace, "daily-page")
    for pair in (assessment_pair, daily_pair):
        if pair["status"] not in {"absent", "ready"}:
            raise WorkspaceRuntimeError(
                f"{pair['kind']} Runtime is {pair['status']}; recover it before starting a new flow"
            )
    if assessment_pair["status"] == "absent" and daily_pair["status"] == "ready":
        raise WorkspaceRuntimeError(
            "Daily Page exists without its Assessment pair; preserving the incomplete Runtime"
        )

    previous_date = None
    if assessment_pair["status"] == "ready":
        with assessment_pair["state_path"].open("r", encoding="utf-8") as stream:
            previous_state = json.load(stream)
        validate_assessment_state(previous_state, mark_saved=False)
        previous_date = previous_state.get("assessment_date")
        if previous_date == assessment_date and not force:
            raise RuntimeError(
                f"Assessment runtime already exists for {assessment_date}; use --force to replace it intentionally"
            )
    if daily_pair["status"] == "ready":
        with daily_pair["state_path"].open("r", encoding="utf-8") as stream:
            validate_daily_page_state(json.load(stream), mark_saved=False)

    state = build_state(assessment_date, prefill)
    clear_pairs = ("daily-page",) if daily_pair["status"] == "ready" else ()
    return write_runtime_pair(workspace, "assessment", state, clear_pairs=clear_pairs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--date", type=parse_date, default=date.today().isoformat())
    parser.add_argument("--task", action="append", default=[], help="repeat to prefill task names")
    parser.add_argument("--tasks-file", type=Path, help="JSON array containing task prefill objects")
    parser.add_argument("--force", action="store_true", help="replace an existing same-date runtime")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    prefill = load_prefill_file(args.tasks_file)
    prefill.extend({"task": task} for task in args.task)
    try:
        html_path, state_path = generate(args.workspace, args.date, prefill, args.force)
    except (OSError, ValueError, RuntimeError, WorkspaceRuntimeError) as error:
        raise SystemExit(f"error: {error}") from error
    print(f"Generated {html_path}")
    print(f"Initialized {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
