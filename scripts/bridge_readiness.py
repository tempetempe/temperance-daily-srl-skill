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
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from persistence_bridge import validate_assessment_state, validate_daily_page_state
from workspace_runtime import (
    WorkspaceRuntimeError,
    ensure_directory,
    ensure_workspace,
    inspect_pair,
    read_workspace_marker,
    recover_pair,
)


DEFAULT_PORT = 8977


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


def _existing_bridge(workspace: Path, page: str) -> dict[str, Any] | None:
    paths = ensure_workspace(workspace)
    marker = read_workspace_marker(paths.workspace)
    metadata = _read_json(paths.bridge_metadata)
    candidates: list[int] = []
    if metadata and isinstance(metadata.get("port"), int):
        candidates.append(metadata["port"])
    if DEFAULT_PORT not in candidates:
        candidates.append(DEFAULT_PORT)
    for port in candidates:
        health = health_at(port)
        if _matches_workspace(health, paths.workspace, marker["workspace_id"]) and (
            "explicit-review-save-v1" not in health.get("capabilities", [])
        ):
            raise WorkspaceRuntimeError(
                "The workspace Bridge predates explicit Review submission. "
                "Preserve saved State; an authorized maintenance session must stop "
                "the old Bridge with the formal stop helper before retrying ensure."
            )
        if _matches_workspace(health, paths.workspace, marker["workspace_id"]) and _page_health(
            port, page, health
        ):
            return {
                "status": "reused",
                "pid": metadata.get("pid") if metadata else None,
                "port": port,
                "base_url": f"http://127.0.0.1:{port}",
                "page_url": f"http://127.0.0.1:{port}/{page}.html",
                "workspace_root": str(paths.workspace),
                "workspace_id": marker["workspace_id"],
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
    with paths.bridge_log.open("ab", buffering=0) as log_stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=log_stream,
            start_new_session=True,
            close_fds=True,
        )

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
                    }
        time.sleep(0.05)

    if process.poll() is None:
        process.terminate()
    raise WorkspaceRuntimeError(
        f"Bridge did not become ready for {paths.workspace}; diagnostic log: {paths.bridge_log}"
    )


def ensure_bridge(
    workspace: Path,
    *,
    page: str,
    default_port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    if page not in {"assessment", "daily-page"}:
        raise ValueError("page must be assessment or daily-page")
    if not 0 <= default_port <= 65535:
        raise ValueError("default port must be between 0 and 65535")
    paths = ensure_workspace(workspace)
    statuses = recover_runtime(paths.workspace)
    if statuses[page] != "ready":
        raise WorkspaceRuntimeError(f"{page} Runtime is not ready: {statuses[page]}")

    existing = _existing_bridge(paths.workspace, page)
    if existing:
        return existing

    requested_port = 0 if _port_is_occupied(default_port) else default_port
    try:
        return _launch_bridge(paths.workspace, page, requested_port)
    except WorkspaceRuntimeError:
        if requested_port == 0:
            raise
        return _launch_bridge(paths.workspace, page, 0)


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


def stop_bridge(workspace: Path) -> dict[str, Any]:
    paths = ensure_workspace(workspace)
    status = bridge_status(paths.workspace)
    if status["status"] != "ready":
        return {"status": "not_running", "workspace_root": str(paths.workspace)}
    pid = status.get("pid")
    port = status.get("port")
    if not isinstance(pid, int) or not isinstance(port, int):
        raise WorkspaceRuntimeError("Bridge metadata has no valid pid/port; refusing to stop a process")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if health_at(port, timeout=0.15) is None:
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
        else:
            result = stop_bridge(args.workspace)
    except (OSError, ValueError, WorkspaceRuntimeError) as error:
        raise SystemExit(f"error: {error}") from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
