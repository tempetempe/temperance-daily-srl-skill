#!/usr/bin/env python3
"""Ensure the Daily SRL Persistence Bridge is healthy for one workspace."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.client import HTTPException
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from persistence_bridge import validate_assessment_state, validate_daily_page_state
from workspace_runtime import (
    PAIR_SPECS,
    WorkspaceRuntimeError,
    ensure_directory,
    ensure_workspace,
    inspect_pair,
    page_context,
    read_workspace_marker,
    recover_pair,
    workspace_paths,
)


DEFAULT_PORT = 8977
REQUIRED_CAPABILITIES = {
    "explicit-review-save-v1", "recoverable-save-v2", "coaching-provenance-v1",
    "separate-review-notes-v1",
}


def _valid_port(value: Any) -> bool:
    return type(value) is int and 1 <= value <= 65535


def _metadata_for_workspace(
    metadata: dict[str, Any] | None, workspace: Path, workspace_id: str
) -> bool:
    return bool(
        metadata
        and metadata.get("workspace_root") == str(workspace.resolve())
        and metadata.get("workspace_id") == workspace_id
    )


def _launch_options(platform: str | None = None) -> tuple[str, dict[str, Any]]:
    """Separate console/session lifetime from the command's redirected streams.

    Neither branch bypasses a host's process cleanup or Windows Job policy.
    A successful readiness check proves availability now, not indefinite lifetime.
    """
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
        "shell": False,
        "env": environment,
    }
    if (platform or sys.platform) == "win32":
        # DETACHED_PROCESS avoids inheriting the transient console. A new
        # process group does not escape an enclosing WorkBuddy Job Object.
        options["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
        return "windows-detached-console", options
    options["start_new_session"] = True
    return "posix-session", options


def _persistent_session_fallback(workspace: Path, port: int) -> dict[str, Any]:
    paths = workspace_paths(workspace)
    return {
        "mode": "host-supported-persistent-terminal",
        "argv": [
            sys.executable, str(SCRIPT_DIR / "persistence_bridge.py"),
            "--workspace", str(paths.workspace), "--port", str(port),
            "--ready-file", str(paths.bridge_metadata),
            "--process-token", uuid.uuid4().hex,
        ],
        "env": {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
        "instruction": (
            "The Agent may run this foreground command in a host-supported persistent "
            "terminal, then verify readiness in a separate invocation. Keep the terminal alive. Do not "
            "claim guaranteed lifetime or bypass host process cleanup. Preserve the "
            "open page and its draft before changing the page URL."
        ),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def health_at(port: int, timeout: float = 0.4) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            value = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return value if isinstance(value, dict) and value.get("ok") is True else None


def _matches_workspace(
    health: dict[str, Any] | None,
    workspace_root: Path,
    workspace_id: str,
    *,
    process_token: str | None = None,
) -> bool:
    if health is None:
        return False
    if health.get("workspace_root") != str(workspace_root.resolve()):
        return False
    if health.get("workspace_id") != workspace_id:
        return False
    if process_token is not None and health.get("process_token") != process_token:
        return False
    return True


def _page_health(port: int, page: str, health: dict[str, Any]) -> bool:
    kind = page
    if health.get("runtime_pairs", {}).get(kind) != "ready":
        return False
    if health.get("state_valid", {}).get(kind) is not True:
        return False
    filename = "assessment.html" if kind == "assessment" else "daily-page.html"
    state_endpoint = (
        "/api/assessment-state" if kind == "assessment" else "/api/daily-page-state"
    )
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/{filename}", timeout=0.7
        ) as response:
            if response.status != 200:
                return False
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{state_endpoint}", timeout=0.7
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _check_page_endpoints(port: int, page: str, expected_context: dict[str, str]) -> str | None:
    """Verify the prepared runtime, writable save client handoff, and State endpoint."""
    base = f"http://127.0.0.1:{port}"
    state_endpoint = f"/api/{page}-state"
    endpoint = f"/{page}.html"
    try:
        with urllib.request.urlopen(base + endpoint, timeout=0.7) as response:
            html = response.read().decode("utf-8")
        _, tag, body = html.partition('<script id="save-context" type="application/json">')
        content, end, _ = body.partition("</script>")
        if (not tag or not end or json.loads(content) != expected_context
                or 'src="save-client.js"' not in html):
            return "served page does not match the prepared page generation"
        endpoint = state_endpoint
        with urllib.request.urlopen(base + endpoint, timeout=0.7) as response:
            state = json.load(response)
            runtime_id = response.headers.get("X-Daily-SRL-Context")
            if not response.headers.get("ETag"):
                return "served State is missing its save revision"
        validator = validate_assessment_state if page == "assessment" else validate_daily_page_state
        state = validator(state, mark_saved=False)
        if (runtime_id != expected_context["runtime_id"]
                or page_context(page, state, expected_context["workspace_id"]) != expected_context):
            return "served State does not match the prepared page generation"
        endpoint = "/save-client.js"
        with urllib.request.urlopen(base + endpoint, timeout=0.7) as response:
            client = response.read()
        if client != (SCRIPT_DIR.parent / "templates" / "save-client.js").read_bytes():
            return "served save client differs from the installed save client"
    except urllib.error.HTTPError as error:
        return f"{endpoint} returned HTTP {error.code}"
    except (OSError, ValueError, HTTPException):
        return f"{endpoint} could not be read or validated"
    return None


def _matches_recorded_process(
    metadata: dict[str, Any] | None, health: dict[str, Any] | None,
    workspace: Path, workspace_id: str,
) -> bool:
    if not _metadata_for_workspace(metadata, workspace, workspace_id):
        return False
    token, pid = metadata.get("process_token"), metadata.get("pid")
    return (
        isinstance(token, str) and bool(token) and type(pid) is int and pid > 0
        and _matches_workspace(health, workspace, workspace_id, process_token=token)
        and health.get("pid") == pid
    )


def verify_page(
    workspace: Path, *, page: str, flow_date: str, runtime_id: str
) -> dict[str, Any]:
    """Read-only WorkBuddy handoff after the page preparation command has ended."""
    paths = workspace_paths(workspace)
    result = {
        "status": "page_not_ready", "page": page, "flow_date": flow_date,
        "page_opened": False, "next_action": "preserve_draft_then_recover_bridge",
    }
    if page not in PAIR_SPECS:
        raise ValueError("page must be assessment or daily-page")
    marker = read_workspace_marker(paths.workspace)
    metadata = _read_json(paths.bridge_metadata)
    if not metadata or not _valid_port(metadata.get("port")):
        return dict(result, error="prepared Bridge is missing its recorded port")
    port = metadata["port"]
    health = health_at(port)
    if not _matches_recorded_process(metadata, health, paths.workspace, marker["workspace_id"]):
        return dict(result, error="prepared Bridge is unavailable or its identity changed")
    _require_bridge_capabilities([(port, health)])
    expected_context = {"workspace_id": marker["workspace_id"], "kind": page, "date": flow_date,
                        "runtime_id": runtime_id}
    error = _check_page_endpoints(port, page, expected_context)
    if error:
        return dict(result, error=error)
    return dict(result, status="ready", next_action="open_and_verify_page",
                runtime_id=runtime_id, port=port, page_url=f"http://127.0.0.1:{port}/{page}.html")


def _port_is_occupied(port: int) -> bool:
    if port == 0:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def recover_runtime(workspace: Path) -> dict[str, str]:
    """Recover safe Runtime Pair cases before starting or reusing a Bridge."""
    validators = {
        "assessment": lambda value: validate_assessment_state(value, mark_saved=False),
        "daily-page": lambda value: validate_daily_page_state(value, mark_saved=False),
    }
    result: dict[str, str] = {}
    for kind in ("assessment", "daily-page"):
        status = inspect_pair(workspace, kind)["status"]
        if status == "absent":
            result[kind] = status
            continue
        recovered = recover_pair(workspace, kind, validators[kind])
        result[kind] = recovered["status"]
    return result


def _workspace_bridges(workspace: Path) -> list[tuple[int, dict[str, Any]]]:
    """Discover only services whose live identity matches this workspace."""
    paths = ensure_workspace(workspace)
    marker = read_workspace_marker(paths.workspace)
    metadata = _read_json(paths.bridge_metadata)
    candidates: list[int] = []
    if (
        _metadata_for_workspace(metadata, paths.workspace, marker["workspace_id"])
        and _valid_port(metadata.get("port"))
    ):
        candidates.append(metadata["port"])
    if DEFAULT_PORT not in candidates:
        candidates.append(DEFAULT_PORT)
    matches = []
    for port in candidates:
        health = health_at(port)
        if _matches_workspace(health, paths.workspace, marker["workspace_id"]):
            matches.append((port, health))
    return matches


def _require_bridge_capabilities(bridges: list[tuple[int, dict[str, Any]]]) -> None:
    for _, health in bridges:
        if not REQUIRED_CAPABILITIES.issubset(health.get("capabilities", [])):
            raise WorkspaceRuntimeError(
                "The workspace Bridge lacks required save/source-preservation capabilities. "
                "Preserve saved State; an authorized maintenance session must stop "
                "the old Bridge with the formal stop helper before retrying ensure."
            )


def require_compatible_bridge(workspace: Path) -> None:
    """Reject an identified older service before changing local page contents.

    This check never starts or stops a process and needs no generated page.
    Foreign services are not ours to stop or assess as a workspace upgrade.
    """
    _require_bridge_capabilities(_workspace_bridges(workspace))


def _existing_bridge(
    workspace: Path, page: str, *, require_recorded: bool = False,
    expected_context: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    paths = ensure_workspace(workspace)
    marker = read_workspace_marker(paths.workspace)
    bridges = _workspace_bridges(paths.workspace)
    _require_bridge_capabilities(bridges)
    metadata = _read_json(paths.bridge_metadata) if require_recorded else None
    for port, health in bridges:
        if require_recorded and (
            not _matches_recorded_process(metadata, health, paths.workspace, marker["workspace_id"])
            or metadata.get("port") != port
        ):
            continue
        if _page_health(port, page, health) and (
            expected_context is None or _check_page_endpoints(port, page, expected_context) is None
        ):
            return {
                "status": "reused",
                "pid": health.get("pid"),
                "port": port,
                "base_url": f"http://127.0.0.1:{port}",
                "page_url": f"http://127.0.0.1:{port}/{page}.html",
                "workspace_root": str(paths.workspace),
                "workspace_id": marker["workspace_id"],
                "launch_mode": "existing-process",
                "lifetime_guarantee": "initial-readiness-only",
                "port_changed": False,
            }
    return None


def _launch_bridge(workspace: Path, page: str, requested_port: int) -> dict[str, Any]:
    paths = ensure_workspace(workspace)
    marker = read_workspace_marker(paths.workspace)
    token = uuid.uuid4().hex
    command = [
        sys.executable,
        str(SCRIPT_DIR / "persistence_bridge.py"),
        "--workspace",
        str(paths.workspace),
        "--port",
        str(requested_port),
        "--ready-file",
        str(paths.bridge_metadata),
        "--process-token",
        token,
    ]
    ensure_directory(paths.bridge_log.parent)
    launch_mode, launch_options = _launch_options()
    try:
        with paths.bridge_log.open("ab", buffering=0) as log_stream:
            process = subprocess.Popen(
                command,
                stdout=log_stream,
                stderr=log_stream,
                **launch_options,
            )
    except OSError as error:
        raise WorkspaceRuntimeError(json.dumps({
            "error": f"Bridge could not start: {error}",
            "workspace_root": str(paths.workspace),
            "diagnostic_log": str(paths.bridge_log),
            "launch_mode": launch_mode,
            "persistent_session_fallback": _persistent_session_fallback(workspace, requested_port),
        }, ensure_ascii=False)) from error

    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        metadata = _read_json(paths.bridge_metadata)
        if metadata and metadata.get("process_token") == token:
            port = metadata.get("port")
            if isinstance(port, int):
                health = health_at(port, timeout=0.6)
                if _matches_workspace(
                    health,
                    paths.workspace,
                    marker["workspace_id"],
                    process_token=token,
                ) and _page_health(port, page, health):
                    return {
                        "status": "started",
                        "pid": process.pid,
                        "port": port,
                        "base_url": f"http://127.0.0.1:{port}",
                        "page_url": f"http://127.0.0.1:{port}/{page}.html",
                        "workspace_root": str(paths.workspace),
                        "workspace_id": marker["workspace_id"],
                        "launch_mode": launch_mode,
                        "lifetime_guarantee": "initial-readiness-only",
                    }
        time.sleep(0.05)

    if process.poll() is None:
        process.terminate()
    raise WorkspaceRuntimeError(json.dumps({
        "error": "Bridge did not become ready",
        "workspace_root": str(paths.workspace),
        "diagnostic_log": str(paths.bridge_log),
        "launch_mode": launch_mode,
        "persistent_session_fallback": _persistent_session_fallback(workspace, requested_port),
    }, ensure_ascii=False))


def ensure_bridge(
    workspace: Path,
    *,
    page: str,
    default_port: int = DEFAULT_PORT,
    persistent_session: bool = False,
    workbuddy_context: dict[str, str] | None = None,
    recover_unhealthy: bool = False,
) -> dict[str, Any]:
    if page not in {"assessment", "daily-page"}:
        raise ValueError("page must be assessment or daily-page")
    if not 0 <= default_port <= 65535:
        raise ValueError("default port must be between 0 and 65535")
    paths = ensure_workspace(workspace)
    statuses = recover_runtime(paths.workspace)
    if statuses[page] != "ready":
        raise WorkspaceRuntimeError(f"{page} Runtime is not ready: {statuses[page]}")

    recovery_context = workbuddy_context if recover_unhealthy else None
    existing = (_existing_bridge(paths.workspace, page, require_recorded=True,
                                 expected_context=recovery_context)
                if workbuddy_context else _existing_bridge(paths.workspace, page))
    if existing:
        return existing

    marker = read_workspace_marker(paths.workspace)
    metadata = _read_json(paths.bridge_metadata)
    previous_port = (
        metadata.get("port") if _metadata_for_workspace(
            metadata, paths.workspace, marker["workspace_id"]
        ) and _valid_port(metadata.get("port")) else None
    )
    if previous_port is not None and recovery_context:
        health = health_at(previous_port)
        if _matches_recorded_process(metadata, health, paths.workspace, marker["workspace_id"]):
            if _check_page_endpoints(previous_port, page, recovery_context) is None:
                existing = _existing_bridge(paths.workspace, page, require_recorded=True,
                                            expected_context=recovery_context)
                if existing:
                    return existing
                raise WorkspaceRuntimeError("Bridge changed during recovery; preserve the page and draft")
            state_path = paths.runtime / PAIR_SPECS[page]["state"]
            current = json.loads(state_path.read_text(encoding="utf-8"))
            if page_context(page, current, marker["workspace_id"]) != recovery_context:
                raise WorkspaceRuntimeError("Page generation changed during recovery; preserve State")
            stopped = stop_bridge(paths.workspace, expected_process_token=metadata["process_token"])
            if stopped["status"] != "stopped" and _port_is_occupied(previous_port):
                raise WorkspaceRuntimeError("Bridge changed during recovery; preserve the page and draft")
    preferred_port = previous_port if previous_port is not None else default_port
    requested_port = 0 if _port_is_occupied(preferred_port) else preferred_port
    if persistent_session:
        result = {
            "status": "needs_persistent_bridge",
            "port": requested_port,
            "workspace_id": marker["workspace_id"],
            "persistent_session": _persistent_session_fallback(paths.workspace, requested_port),
        }
    else:
        try:
            result = _launch_bridge(paths.workspace, page, requested_port)
        except WorkspaceRuntimeError:
            if requested_port == 0 or recover_unhealthy:
                raise
            result = _launch_bridge(paths.workspace, page, 0)
    result["port_changed"] = previous_port is not None and result["port"] != previous_port
    if result["port_changed"]:
        result["origin_notice"] = (
            "The previous port is unavailable. Keep the old page open and preserve or "
            "export its draft before opening the new URL; browser draft storage does "
            "not automatically transfer between ports."
        )
    return result


def bridge_status(workspace: Path) -> dict[str, Any]:
    paths = ensure_workspace(workspace)
    marker = read_workspace_marker(paths.workspace)
    metadata = _read_json(paths.bridge_metadata)
    if not metadata or not isinstance(metadata.get("port"), int):
        return {"status": "not_running", "workspace_root": str(paths.workspace)}
    health = health_at(metadata["port"])
    if not _matches_workspace(health, paths.workspace, marker["workspace_id"]):
        return {"status": "not_running", "workspace_root": str(paths.workspace)}
    return {"status": "ready", **metadata, "health": health}


def stop_bridge(workspace: Path, *, expected_process_token: str | None = None) -> dict[str, Any]:
    paths = ensure_workspace(workspace)
    status = bridge_status(paths.workspace)
    if status["status"] != "ready":
        return {"status": "not_running", "workspace_root": str(paths.workspace)}
    pid = status.get("pid")
    port = status.get("port")
    if type(pid) is not int or pid <= 0 or pid == os.getpid() or not _valid_port(port):
        raise WorkspaceRuntimeError("Bridge metadata has no valid pid/port; refusing to stop a process")
    token = status.get("process_token")
    if not isinstance(token, str) or not token:
        raise WorkspaceRuntimeError("Bridge has no process token; refusing to stop an unverified process")
    if expected_process_token is not None and expected_process_token != token:
        raise WorkspaceRuntimeError("Bridge process identity changed; refusing to stop a process")
    # On Windows SIGTERM terminates the selected PID immediately. Require the
    # live token; RC7 also reports its PID, while legacy Bridges lack that field.
    health = health_at(port)
    marker = read_workspace_marker(paths.workspace)
    if not _matches_workspace(
        health, paths.workspace, marker["workspace_id"], process_token=token
    ) or (health.get("pid") is not None and health.get("pid") != pid):
        raise WorkspaceRuntimeError("Bridge process identity changed; refusing to stop a process")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"status": "not_running", "workspace_root": str(paths.workspace)}
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not _matches_workspace(
            health_at(port, timeout=0.15), paths.workspace, marker["workspace_id"],
            process_token=token,
        ):
            return {"status": "stopped", "pid": pid, "port": port}
        time.sleep(0.05)
    raise WorkspaceRuntimeError(f"Bridge process {pid} did not stop after SIGTERM")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    ensure = subparsers.add_parser("ensure", help="ensure a healthy Bridge and print the page URL")
    ensure.add_argument("--page", choices=("assessment", "daily-page"), required=True)
    ensure.add_argument("--default-port", type=int, default=DEFAULT_PORT)
    verify = subparsers.add_parser("verify-page", help="check the prepared WorkBuddy page without writes")
    verify.add_argument("--page", choices=("assessment", "daily-page"), required=True)
    verify.add_argument("--date", required=True)
    verify.add_argument("--runtime-id", required=True)
    subparsers.add_parser("status", help="show Bridge health for this workspace")
    subparsers.add_parser("stop", help="stop only the verified Bridge for this workspace")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "ensure":
            result = ensure_bridge(
                args.workspace, page=args.page, default_port=args.default_port
            )
        elif args.command == "status":
            result = bridge_status(args.workspace)
        elif args.command == "verify-page":
            result = verify_page(args.workspace, page=args.page, flow_date=args.date,
                                 runtime_id=args.runtime_id)
        else:
            result = stop_bridge(args.workspace)
    except (OSError, ValueError, WorkspaceRuntimeError) as error:
        if args.command != "verify-page":
            raise SystemExit(f"error: {error}") from error
        result = {"status": "page_not_ready", "page": args.page, "flow_date": args.date,
                  "page_opened": False, "next_action": "preserve_draft_then_recover_bridge",
                  "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "page_not_ready" else 0


if __name__ == "__main__":
    raise SystemExit(main())
