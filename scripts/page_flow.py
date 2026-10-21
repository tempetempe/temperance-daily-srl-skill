#!/usr/bin/env python3
"""Prepare one Daily SRL page and its Bridge without replacing same-day edits."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bridge_readiness import DEFAULT_PORT, ensure_bridge, recover_runtime
from generate_assessment import generate as generate_assessment, load_prefill_file, parse_date
from generate_daily_page import generate as generate_daily_page
from persistence_bridge import validate_assessment_state, validate_daily_page_state
from workspace_runtime import WorkspaceRuntimeError, ensure_workspace, refresh_page_client


def _load_state(path: Path, validator: Any) -> dict[str, Any]:
    return validator(json.loads(path.read_text(encoding="utf-8")), mark_saved=False)


def prepare_page(
    workspace: Path,
    page: str,
    *,
    assessment_date: str | None = None,
    prefill: list[dict[str, Any]] | None = None,
    coaching_file: Path | None = None,
    default_port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    """Compose existing generation, conservative recovery and readiness helpers.

    A successful result means a verified URL is ready for the host to open.
    It never asserts that a browser was opened. Bridge failures report the
    already-preserved local page separately so a retry does not regenerate it.
    """
    if page not in {"assessment", "daily-page"}:
        raise ValueError("page must be assessment or daily-page")
    if not 0 <= default_port <= 65535:
        raise ValueError("default port must be between 0 and 65535")
    requested_date = parse_date(assessment_date or date.today().isoformat())
    prefill = [] if prefill is None else prefill
    paths = ensure_workspace(workspace)
    statuses = recover_runtime(paths.workspace)
    assessment_path = paths.runtime / "assessment-state.json"
    daily_path = paths.runtime / "daily-page-state.json"
    assessment = (
        _load_state(assessment_path, validate_assessment_state)
        if statuses["assessment"] == "ready" else None
    )
    if assessment is None and statuses["daily-page"] != "absent":
        raise WorkspaceRuntimeError("Daily Page exists without its Assessment; preserving Runtime")

    operation = "resumed"
    ignored_inputs: list[str] = []
    if page == "assessment":
        if assessment is None or assessment["assessment_date"] != requested_date:
            generate_assessment(paths.workspace, requested_date, prefill)
            assessment = _load_state(assessment_path, validate_assessment_state)
            operation = "generated"
        elif prefill:
            ignored_inputs.append("prefill: existing same-date Assessment was preserved")
        state = assessment
        flow_date = state["assessment_date"]
        state_path = assessment_path
        tasks = state["tasks"]
    else:
        if assessment is None:
            raise WorkspaceRuntimeError("Assessment Runtime does not exist")
        if not assessment.get("saved_at"):
            raise WorkspaceRuntimeError("Save Assessment before preparing a new Daily Page")
        flow_date = assessment["assessment_date"]
        if statuses["daily-page"] == "absent":
            generate_daily_page(paths.workspace, coaching_file=coaching_file)
            operation = "generated"
        elif coaching_file is not None:
            ignored_inputs.append("coaching_file: existing Daily Page was preserved")
        state = _load_state(daily_path, validate_daily_page_state)
        if state["page_date"] != flow_date:
            raise WorkspaceRuntimeError("Daily Page and Assessment dates differ; preserving both")
        if [task["task_id"] for task in state["tasks"]] != [
            task["task_id"] for task in assessment["tasks"]
        ]:
            raise WorkspaceRuntimeError("Daily Page and Assessment tasks differ; preserving both")
        state_path = daily_path
        tasks = [{"task_id": task["task_id"], "final_plan": task["final_plan"]}
                 for task in state["tasks"]]

    client_upgraded = refresh_page_client(paths.workspace, page, state)
    result: dict[str, Any] = {
        "status": "ready",
        "page": page,
        "operation": operation,
        "workspace_root": str(paths.workspace),
        "flow_date": flow_date,
        "html_path": str(paths.runtime / (page + ".html")),
        "state_path": str(state_path),
        "saved_at": state.get("saved_at"),
        "tasks": tasks,
        "ignored_inputs": ignored_inputs,
        "local_content_ready": True,
        "page_opened": False,
        "page_client_upgraded": client_upgraded,
    }
    try:
        bridge = ensure_bridge(paths.workspace, page=page, default_port=default_port)
    except (OSError, ValueError, RuntimeError) as error:
        result.update(status="page_not_ready", next_action="retry_readiness", error=str(error))
        try:
            diagnostic = json.loads(str(error))
        except (ValueError, TypeError):
            diagnostic = {}
        if isinstance(diagnostic, dict):
            for key in ("persistent_session_fallback", "diagnostic_log", "launch_mode"):
                if key in diagnostic:
                    result[key] = diagnostic[key]
        return result
    result.update(page_url=bridge["page_url"], bridge_status=bridge["status"],
                  workspace_id=bridge["workspace_id"], next_action="open_page_url")
    for key in ("launch_mode", "lifetime_guarantee", "port_changed", "origin_notice"):
        if key in bridge:
            result[key] = bridge[key]
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="page", required=True)
    assessment = commands.add_parser("assessment", help="create or resume Assessment and prepare its URL")
    assessment.add_argument("--date", type=parse_date, default=date.today().isoformat())
    assessment.add_argument("--task", action="append", default=[])
    assessment.add_argument("--tasks-file", type=Path)
    daily = commands.add_parser("daily-page", help="create from saved Assessment or resume Daily Page")
    daily.add_argument("--coaching-file", type=Path)
    for command in (assessment, daily):
        command.add_argument("--default-port", type=int, default=DEFAULT_PORT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        prefill = load_prefill_file(args.tasks_file) if args.page == "assessment" else []
        if args.page == "assessment":
            prefill.extend({"task": task} for task in args.task)
        result = prepare_page(args.workspace, args.page, assessment_date=getattr(args, "date", None),
                              prefill=prefill, coaching_file=getattr(args, "coaching_file", None),
                              default_port=args.default_port)
    except (OSError, ValueError, RuntimeError) as error:
        result = {"status": "blocked", "page": args.page, "page_opened": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
