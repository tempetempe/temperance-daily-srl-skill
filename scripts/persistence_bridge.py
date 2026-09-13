#!/usr/bin/env python3
"""Serve Daily SRL runtime pages and persist their validated complete state."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workspace_runtime import (
    WorkspaceRuntimeError,
    atomic_write_json as runtime_atomic_write_json,
    ensure_workspace,
    inspect_pair,
    mark_runtime_user_saved,
    read_workspace_marker,
    runtime_summary,
)


MAX_BODY_BYTES = 1_000_000
CONFIDENCE_VALUES = {"confident", "uncertain", "not_confident"}
VISUAL_BY_CONFIDENCE = {
    "confident": "normal",
    "uncertain": "notice",
    "not_confident": "focus",
}
COMPLETION_VALUES = {"", "completed", "partial", "not_completed", "changed"}
EXPECTATION_VALUES = {"", "better", "as_expected", "below_expected"}
TASK_ID_PATTERN = re.compile(r"^task-(\d{8})-(\d{3,})$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    runtime_atomic_write_json(path, payload)


def require_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def validate_assessment_state(payload: Any, *, mark_saved: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("state must be a JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")

    assessment_date = require_string(payload.get("assessment_date"), "assessment_date")
    parsed_date = date.fromisoformat(assessment_date)
    assessment_date = parsed_date.isoformat()
    date_part = assessment_date.replace("-", "")

    generated_at = require_string(payload.get("generated_at"), "generated_at")
    saved_at = payload.get("saved_at")
    if saved_at is not None and not isinstance(saved_at, str):
        raise ValueError("saved_at must be a string or null")
    require_complete_tasks = mark_saved or saved_at is not None
    next_task_number = payload.get("next_task_number")
    if isinstance(next_task_number, bool) or not isinstance(next_task_number, int) or next_task_number < 1:
        raise ValueError("next_task_number must be a positive integer")

    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("tasks must be an array")

    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    highest_number = 0
    for index, raw_task in enumerate(raw_tasks):
        prefix = f"tasks[{index}]"
        if not isinstance(raw_task, dict):
            raise ValueError(f"{prefix} must be an object")
        task_id = require_string(raw_task.get("task_id"), f"{prefix}.task_id")
        match = TASK_ID_PATTERN.fullmatch(task_id)
        if not match or match.group(1) != date_part:
            raise ValueError(f"{prefix}.task_id must match task-{date_part}-NNN")
        if task_id in seen_ids:
            raise ValueError(f"duplicate task_id: {task_id}")
        seen_ids.add(task_id)
        highest_number = max(highest_number, int(match.group(2)))

        task_text = require_string(raw_task.get("task"), f"{prefix}.task")
        planned_start = require_string(
            raw_task.get("planned_start"),
            f"{prefix}.planned_start",
            allow_empty=not require_complete_tasks,
        )
        duration = raw_task.get("estimated_duration_minutes")
        duration_is_valid = isinstance(duration, int) and not isinstance(duration, bool) and duration >= 1
        if require_complete_tasks and not duration_is_valid:
            raise ValueError(f"{prefix}.estimated_duration_minutes must be a positive integer")
        if not require_complete_tasks and duration is not None and not duration_is_valid:
            raise ValueError(f"{prefix}.estimated_duration_minutes must be null or a positive integer")
        confidence = raw_task.get("confidence")
        allowed_confidence = CONFIDENCE_VALUES if require_complete_tasks else CONFIDENCE_VALUES | {""}
        if confidence not in allowed_confidence:
            raise ValueError(f"{prefix}.confidence is invalid")

        tasks.append(
            {
                "task_id": task_id,
                "task": task_text,
                "planned_start": planned_start,
                "estimated_duration_minutes": duration,
                "confidence": confidence,
            }
        )

    if next_task_number <= highest_number:
        raise ValueError("next_task_number must be greater than every allocated task number")

    return {
        "schema_version": 1,
        "assessment_date": assessment_date,
        "generated_at": generated_at,
        "saved_at": utc_now() if mark_saved else saved_at,
        "next_task_number": next_task_number,
        "tasks": tasks,
    }


# Compatibility alias retained for existing callers.
validate_state = validate_assessment_state


def require_text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(require_string(item, f"{field}[{index}]"))
    return result


def require_complete_review(state: dict[str, Any]) -> None:
    """Drafts may be incomplete; an explicit Review submission may not."""
    for task in state["tasks"]:
        review = task["review"]
        if not review["completion"]:
            raise ValueError("请先填写每项任务的完成情况。")
        if review["actual_duration_minutes"] is None:
            raise ValueError("请先填写每项任务的实际用时。")
        if not review["expectation"]:
            raise ValueError("请先填写每项任务与原先预期相比的情况。")


def validate_daily_page_state(
    payload: Any,
    *,
    mark_saved: bool,
    baseline: dict[str, Any] | None = None,
    require_complete: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("state must be a JSON object")
    if payload.get("schema_version") != 2:
        raise ValueError("Daily Page schema_version must be 2")
    page_date = date.fromisoformat(require_string(payload.get("page_date"), "page_date")).isoformat()
    date_part = page_date.replace("-", "")
    generated_at = require_string(payload.get("generated_at"), "generated_at")
    saved_at = payload.get("saved_at")
    if saved_at is not None and not isinstance(saved_at, str):
        raise ValueError("saved_at must be a string or null")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("tasks must be an array")

    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_task in enumerate(raw_tasks):
        prefix = f"tasks[{index}]"
        if not isinstance(raw_task, dict):
            raise ValueError(f"{prefix} must be an object")
        task_id = require_string(raw_task.get("task_id"), f"{prefix}.task_id")
        match = TASK_ID_PATTERN.fullmatch(task_id)
        if not match or match.group(1) != date_part:
            raise ValueError(f"{prefix}.task_id must match task-{date_part}-NNN")
        if task_id in seen_ids:
            raise ValueError(f"duplicate task_id: {task_id}")
        seen_ids.add(task_id)

        source_confidence = raw_task.get("source_confidence")
        if source_confidence not in CONFIDENCE_VALUES:
            raise ValueError(f"{prefix}.source_confidence is invalid")
        visual_state = raw_task.get("visual_state")
        if visual_state != VISUAL_BY_CONFIDENCE[source_confidence]:
            raise ValueError(f"{prefix}.visual_state does not match source_confidence")

        coaching = raw_task.get("coaching_result")
        if coaching is None:
            coaching_result = None
        elif isinstance(coaching, dict):
            coaching_result = {
                "main_obstacles": require_text_list(
                    coaching.get("main_obstacles"), f"{prefix}.coaching_result.main_obstacles"
                ),
                "actions": require_text_list(coaching.get("actions"), f"{prefix}.coaching_result.actions"),
                "resources": require_text_list(
                    coaching.get("resources"), f"{prefix}.coaching_result.resources"
                ),
                "help_or_communication": require_text_list(
                    coaching.get("help_or_communication"),
                    f"{prefix}.coaching_result.help_or_communication",
                ),
                "other_confirmed_information": require_text_list(
                    coaching.get("other_confirmed_information"),
                    f"{prefix}.coaching_result.other_confirmed_information",
                ),
            }
        else:
            raise ValueError(f"{prefix}.coaching_result must be an object or null")

        plan = raw_task.get("final_plan")
        if not isinstance(plan, dict):
            raise ValueError(f"{prefix}.final_plan must be an object")
        unknown_plan_fields = set(plan) - {
            "task",
            "planned_start",
            "estimated_duration_minutes",
        }
        if unknown_plan_fields:
            raise ValueError(
                f"{prefix}.final_plan has unsupported fields: {sorted(unknown_plan_fields)}"
            )
        duration = plan.get("estimated_duration_minutes")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            raise ValueError(f"{prefix}.final_plan.estimated_duration_minutes must be a positive integer")
        final_plan = {
            "task": require_string(plan.get("task"), f"{prefix}.final_plan.task"),
            "planned_start": require_string(
                plan.get("planned_start"), f"{prefix}.final_plan.planned_start"
            ),
            "estimated_duration_minutes": duration,
        }
        review = raw_task.get("review")
        if not isinstance(review, dict):
            raise ValueError(f"{prefix}.review must be an object")
        completion = review.get("completion")
        if completion not in COMPLETION_VALUES:
            raise ValueError(f"{prefix}.review.completion is invalid")
        actual_duration = review.get("actual_duration_minutes")
        if actual_duration is not None and (
            isinstance(actual_duration, bool)
            or not isinstance(actual_duration, int)
            or actual_duration < 0
        ):
            raise ValueError(
                f"{prefix}.review.actual_duration_minutes must be null or a non-negative integer"
            )
        expectation = review.get("expectation")
        if expectation not in EXPECTATION_VALUES:
            raise ValueError(f"{prefix}.review.expectation is invalid")
        normalized_review = {
            "completion": completion,
            "completion_details": require_string(
                review.get("completion_details"),
                f"{prefix}.review.completion_details",
                allow_empty=True,
            ),
            "actual_duration_minutes": actual_duration,
            "expectation": expectation,
            "task_note": require_string(
                review.get("task_note"), f"{prefix}.review.task_note", allow_empty=True
            ),
        }
        tasks.append(
            {
                "task_id": task_id,
                "source_confidence": source_confidence,
                "visual_state": visual_state,
                "coaching_result": coaching_result,
                "final_plan": final_plan,
                "review": normalized_review,
            }
        )

    result = {
        "schema_version": 2,
        "page_date": page_date,
        "generated_at": generated_at,
        "saved_at": utc_now() if mark_saved else saved_at,
        "tasks": tasks,
        "daily_diary": require_string(
            payload.get("daily_diary"), "daily_diary", allow_empty=True
        ),
    }
    if baseline is not None:
        baseline_ids = [item["task_id"] for item in baseline["tasks"]]
        submitted_ids = [item["task_id"] for item in tasks]
        if submitted_ids != baseline_ids:
            raise ValueError("Daily Page task IDs and order cannot change")
        if result["page_date"] != baseline["page_date"]:
            raise ValueError("Daily Page date cannot change")
        for index, task in enumerate(tasks):
            original = baseline["tasks"][index]
            if task["source_confidence"] != original["source_confidence"]:
                raise ValueError(f"tasks[{index}].source_confidence cannot change")
            if task["visual_state"] != original["visual_state"]:
                raise ValueError(f"tasks[{index}].visual_state cannot change")
    review_saved_at = payload.get("review_saved_at")
    if review_saved_at is not None:
        if not isinstance(review_saved_at, str) or not review_saved_at.strip():
            raise ValueError("review_saved_at must be a non-empty timestamp or null")
        datetime.fromisoformat(review_saved_at.replace("Z", "+00:00"))
    if require_complete:
        require_complete_review(result)
    if mark_saved:
        # Only the explicit API mode can create a submission marker. Client
        # timestamps cannot promote drafts; any content change invalidates it.
        unchanged = baseline is not None and all(
            result[key] == baseline[key] for key in ("tasks", "daily_diary")
        )
        result["review_saved_at"] = (
            result["saved_at"] if require_complete
            else baseline.get("review_saved_at") if unchanged else None
        )
    elif "review_saved_at" in payload:
        result["review_saved_at"] = review_saved_at
        if review_saved_at:
            require_complete_review(result)
    return result


class DailySRLHandler(SimpleHTTPRequestHandler):
    server_version = "DailySRLBridge/3.0"

    def __init__(
        self,
        *args: Any,
        runtime_dir: Path,
        workspace_root: Path,
        workspace_id: str,
        process_token: str,
        **kwargs: Any,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.workspace_root = workspace_root
        self.workspace_id = workspace_id
        self.process_token = process_token
        self.assessment_state_path = runtime_dir / "assessment-state.json"
        self.daily_page_state_path = runtime_dir / "daily-page-state.json"
        super().__init__(*args, directory=str(runtime_dir), **kwargs)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_assessment_state(self) -> dict[str, Any]:
        with self.assessment_state_path.open("r", encoding="utf-8") as stream:
            return validate_assessment_state(json.load(stream), mark_saved=False)

    def read_daily_page_state(self) -> dict[str, Any]:
        with self.daily_page_state_path.open("r", encoding="utf-8") as stream:
            return validate_daily_page_state(json.load(stream), mark_saved=False)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            pairs = runtime_summary(self.workspace_root)
            state_validity: dict[str, bool | None] = {
                "assessment": None,
                "daily-page": None,
            }
            if pairs["assessment"] == "ready":
                try:
                    self.read_assessment_state()
                except (json.JSONDecodeError, ValueError, OSError):
                    state_validity["assessment"] = False
                else:
                    state_validity["assessment"] = True
            if pairs["daily-page"] == "ready":
                try:
                    self.read_daily_page_state()
                except (json.JSONDecodeError, ValueError, OSError):
                    state_validity["daily-page"] = False
                else:
                    state_validity["daily-page"] = True
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "workspace_root": str(self.workspace_root),
                    "workspace_id": self.workspace_id,
                    "runtime_dir": str(self.runtime_dir),
                    "process_token": self.process_token,
                    "capabilities": ["explicit-review-save-v1"],
                    "runtime_pairs": pairs,
                    "state_valid": state_validity,
                },
            )
            return
        if path == "/api/assessment-state":
            try:
                state = self.read_assessment_state()
            except FileNotFoundError:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "assessment state does not exist"})
            except (json.JSONDecodeError, ValueError, OSError) as error:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"invalid state on disk: {error}"})
            else:
                self.send_json(HTTPStatus.OK, state)
            return
        if path == "/api/daily-page-state":
            try:
                state = self.read_daily_page_state()
            except FileNotFoundError:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "daily page state does not exist"})
            except (json.JSONDecodeError, ValueError, OSError) as error:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"invalid state on disk: {error}"})
            else:
                self.send_json(HTTPStatus.OK, state)
            return
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            destination = (
                "/assessment.html"
                if (self.runtime_dir / "assessment.html").is_file()
                else "/daily-page.html"
            )
            self.send_header("Location", destination)
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in {"/api/assessment-state", "/api/daily-page-state"}:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
            return
        if length < 1 or length > MAX_BODY_BYTES:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body size is invalid"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            if path == "/api/assessment-state":
                state = validate_assessment_state(payload, mark_saved=True)
                state_path = self.assessment_state_path
            else:
                baseline = self.read_daily_page_state()
                save_mode = self.headers.get("X-Daily-SRL-Save", "draft")
                if save_mode not in {"draft", "review"}:
                    raise ValueError("invalid Daily SRL save mode")
                state = validate_daily_page_state(
                    payload, mark_saved=True, baseline=baseline,
                    require_complete=save_mode == "review",
                )
                state_path = self.daily_page_state_path
            kind = "assessment" if path == "/api/assessment-state" else "daily-page"
            mark_runtime_user_saved(self.workspace_root, kind)
            atomic_write_json(state_path, state)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except (OSError, WorkspaceRuntimeError) as error:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"could not write state: {error}"})
            return
        self.send_json(HTTPStatus.OK, {"ok": True, "state": state})

    def log_message(self, format: str, *args: Any) -> None:
        super().log_message(format, *args)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def create_server(
    workspace: Path,
    port: int,
    *,
    process_token: str = "",
) -> ReusableThreadingHTTPServer:
    paths = ensure_workspace(workspace)
    summary = runtime_summary(paths.workspace)
    incomplete = {kind: status for kind, status in summary.items() if status not in {"absent", "ready"}}
    if incomplete:
        raise WorkspaceRuntimeError(f"Runtime Pair is incomplete: {incomplete}")
    if not any(status == "ready" for status in summary.values()):
        raise FileNotFoundError(f"generate a Daily SRL runtime page first: {paths.runtime}")
    if summary["assessment"] == "ready":
        state_path = paths.runtime / "assessment-state.json"
        with state_path.open("r", encoding="utf-8") as stream:
            validate_assessment_state(json.load(stream), mark_saved=False)
    if summary["daily-page"] == "ready":
        with (paths.runtime / "daily-page-state.json").open("r", encoding="utf-8") as stream:
            validate_daily_page_state(json.load(stream), mark_saved=False)
    marker = read_workspace_marker(paths.workspace)
    handler = partial(
        DailySRLHandler,
        runtime_dir=paths.runtime,
        workspace_root=paths.workspace,
        workspace_id=marker["workspace_id"],
        process_token=process_token,
    )
    return ReusableThreadingHTTPServer(("127.0.0.1", port), handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8977)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--process-token", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 0 <= args.port <= 65535:
        raise SystemExit("error: port must be between 0 and 65535")
    try:
        server = create_server(args.workspace, args.port, process_token=args.process_token)
    except (FileNotFoundError, OSError, ValueError, WorkspaceRuntimeError) as error:
        raise SystemExit(f"error: {error}") from error
    actual_port = int(server.server_address[1])
    paths = ensure_workspace(args.workspace)
    marker = read_workspace_marker(paths.workspace)
    ready_payload = {
        "schema_version": 1,
        "pid": os.getpid(),
        "port": actual_port,
        "base_url": f"http://127.0.0.1:{actual_port}",
        "workspace_root": str(paths.workspace),
        "workspace_id": marker["workspace_id"],
        "process_token": args.process_token,
        "started_at": utc_now(),
    }
    if args.ready_file:
        runtime_atomic_write_json(args.ready_file, ready_payload)
    print(f"Daily SRL Persistence Bridge: http://127.0.0.1:{actual_port}/assessment.html", flush=True)
    if (paths.runtime / "daily-page.html").is_file():
        print(f"Daily Page: http://127.0.0.1:{actual_port}/daily-page.html", flush=True)
    print(f"Runtime: {paths.runtime}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
