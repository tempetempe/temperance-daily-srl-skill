#!/usr/bin/env python3
"""Manage Daily SRL workspace bootstrap, runtime pairs, and conservative recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from platform_lock import nonblocking_file_lock


sys.dont_write_bytecode = True


WORKSPACE_VERSION = 1
LAYOUT_VERSION = 2
RUNTIME_VERSION = 1
WORKSPACE_TYPE = "daily-srl"
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"
DAILY_PAGE_VIEW = "process-and-task-note-v1"


class WorkspaceRuntimeError(RuntimeError):
    """Raised when workspace or runtime safety invariants are not satisfied."""


@dataclass(frozen=True)
class WorkspacePaths:
    workspace: Path
    root: Path
    marker: Path
    runtime: Path
    structured_records: Path
    learning_records: Path
    legacy_learning_records: Path
    manifest: Path
    bridge_metadata: Path
    bridge_log: Path
    recovery_log: Path
    migration_log: Path
    migration_pending: Path

    @property
    def internal_root(self) -> Path:
        return self.root


PAIR_SPECS = {
    "assessment": {
        "state": "assessment-state.json",
        "html": "assessment.html",
        "template": "assessment-template.html",
        "token": "__ASSESSMENT_STATE_JSON__",
        "script_id": "initial-assessment-state",
        "date_field": "assessment_date",
    },
    "daily-page": {
        "state": "daily-page-state.json",
        "html": "daily-page.html",
        "template": "daily-page-template.html",
        "token": "__DAILY_PAGE_STATE_JSON__",
        "script_id": "initial-state",
        "date_field": "page_date",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def workspace_paths(workspace: Path) -> WorkspacePaths:
    resolved = workspace.expanduser().resolve()
    root = resolved / ".daily-srl"
    return WorkspacePaths(
        workspace=resolved,
        root=root,
        marker=root / "workspace.json",
        runtime=root / "runtime",
        structured_records=root / "structured-records",
        learning_records=resolved / "Learning Records",
        legacy_learning_records=root / "learning-records",
        manifest=root / "runtime" / RUNTIME_MANIFEST_NAME,
        bridge_metadata=root / "bridge.json",
        bridge_log=root / "bridge.log",
        recovery_log=root / "recovery-log.jsonl",
        migration_log=root / "migration-log.jsonl",
        migration_pending=root / "migration-pending.json",
    )


def _assert_authorized_workspace(workspace: Path) -> Path:
    """Resolve exactly the host-authorized path without searching or fallback."""
    resolved = workspace.expanduser().resolve()
    package_root = Path(__file__).resolve().parents[1]
    if resolved == package_root or package_root in resolved.parents:
        raise WorkspaceRuntimeError(
            f"the installed Skill package is read-only and cannot be used as a Workspace: {resolved}"
        )
    if not resolved.exists():
        raise WorkspaceRuntimeError(f"workspace does not exist: {resolved}")
    if not resolved.is_dir():
        raise WorkspaceRuntimeError(f"workspace is not a directory: {resolved}")
    return resolved


def ensure_directory(path: Path) -> Path:
    """Create a directory without calling mkdir for already-existing paths.

    Some host sandboxes broker ``Path.mkdir`` and raise ``EEXIST`` before
    Python can honor ``exist_ok=True``.  Readiness must remain idempotent in
    those hosts without runtime monkey-patches or sandbox bypasses, so create
    only the missing path components and verify any concurrent creation.
    """
    target = Path(path)
    if target.exists():
        if not target.is_dir():
            raise WorkspaceRuntimeError(f"required directory is a file: {target}")
        return target

    missing: list[Path] = []
    cursor = target
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise WorkspaceRuntimeError(f"could not resolve an existing parent for: {target}")
        cursor = parent
    if not cursor.is_dir():
        raise WorkspaceRuntimeError(f"directory parent is not a directory: {cursor}")

    for directory in reversed(missing):
        try:
            directory.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            if not directory.is_dir():
                raise WorkspaceRuntimeError(f"required directory is a file: {directory}")
    return target


def _stage_text(path: Path, content: str) -> Path:
    ensure_directory(path.parent)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=f".{uuid.uuid4().hex}.tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def atomic_write_text(path: Path, content: str) -> None:
    temporary_path = _stage_text(path, content)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _append_event(path: Path, payload: dict[str, Any]) -> None:
    """Append a small operational audit event without touching learner data."""
    ensure_directory(path.parent)
    line = json.dumps({"at": utc_now(), **payload}, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def replace_bundle(outputs: dict[Path, str], *, remove: Iterable[Path] = ()) -> None:
    """Stage all outputs, then replace/remove as one rollback-capable bundle."""
    remove_paths = list(dict.fromkeys(remove))
    targets = list(dict.fromkeys([*outputs, *remove_paths]))
    originals = {path: path.read_bytes() if path.exists() else None for path in targets}
    staged: dict[Path, Path] = {}
    changed: list[Path] = []
    try:
        for path, content in outputs.items():
            staged[path] = _stage_text(path, content)
        for path in outputs:
            os.replace(staged[path], path)
            changed.append(path)
        for path in remove_paths:
            if path.exists():
                path.unlink()
                changed.append(path)
    except BaseException:
        for path in reversed(changed):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                rollback = _stage_text(path, original.decode("utf-8"))
                os.replace(rollback, path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except json.JSONDecodeError as error:
        raise WorkspaceRuntimeError(f"{label} JSON is damaged: {path}: {error}") from error
    except OSError as error:
        raise WorkspaceRuntimeError(f"could not read {label}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise WorkspaceRuntimeError(f"{label} must be a JSON object: {path}")
    return value


def _validate_marker(marker: dict[str, Any], path: Path) -> dict[str, Any]:
    if marker.get("workspace_type") != WORKSPACE_TYPE:
        raise WorkspaceRuntimeError(f"workspace marker type conflicts with Daily SRL: {path}")
    if marker.get("workspace_version") != WORKSPACE_VERSION:
        raise WorkspaceRuntimeError(f"unsupported workspace marker version: {path}")
    if marker.get("runtime_version") != RUNTIME_VERSION:
        raise WorkspaceRuntimeError(f"unsupported runtime version: {path}")
    for field in ("workspace_id", "created_at", "last_used_at"):
        if not isinstance(marker.get(field), str) or not marker[field].strip():
            raise WorkspaceRuntimeError(f"workspace marker field {field} is invalid: {path}")
    layout_version = marker.get("layout_version", 1)
    if layout_version not in {1, LAYOUT_VERSION}:
        raise WorkspaceRuntimeError(f"unsupported workspace layout version: {path}")
    return marker


def _marker_layout_version(marker: dict[str, Any]) -> int:
    return int(marker.get("layout_version", 1))


def _validate_workspace_shell(workspace: Path) -> WorkspacePaths:
    resolved = _assert_authorized_workspace(workspace)
    paths = workspace_paths(resolved)
    if paths.root.exists() and not paths.root.is_dir():
        raise WorkspaceRuntimeError(f"Daily SRL path is not a directory: {paths.root}")
    if paths.marker.exists() and not paths.marker.is_file():
        raise WorkspaceRuntimeError(f"workspace marker is not a file: {paths.marker}")
    return paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_files(paths: WorkspacePaths) -> list[Path]:
    if not paths.legacy_learning_records.exists():
        return []
    if not paths.legacy_learning_records.is_dir():
        raise WorkspaceRuntimeError(
            f"legacy Learning Record path is not a directory: {paths.legacy_learning_records}"
        )
    files: list[Path] = []
    for item in sorted(paths.legacy_learning_records.rglob("*")):
        if item.is_symlink():
            raise WorkspaceRuntimeError(f"legacy Learning Record contains a symlink: {item}")
        if item.is_file():
            files.append(item)
    return files


def migrate_workspace_layout(workspace: Path) -> dict[str, Any]:
    """Safely migrate the one canonical Markdown directory from layout 1 to layout 2."""
    paths = _validate_workspace_shell(workspace)
    if not paths.marker.exists():
        raise WorkspaceRuntimeError(
            f"workspace marker is missing; bootstrap the authorized Workspace first: {paths.marker}"
        )
    marker = _validate_marker(_load_json(paths.marker, "workspace marker"), paths.marker)
    if _marker_layout_version(marker) == LAYOUT_VERSION and not paths.migration_pending.exists():
        ensure_directory(paths.learning_records)
        return {
            "status": "already_current",
            "layout_version": LAYOUT_VERSION,
            "migrated_files": [],
        }

    legacy_files = _legacy_files(paths)
    if paths.learning_records.exists() and not paths.learning_records.is_dir():
        raise WorkspaceRuntimeError(
            f"visible Learning Records path is not a directory: {paths.learning_records}"
        )

    conflicts: list[dict[str, str]] = []
    planned: list[tuple[Path, Path, str]] = []
    pending = None
    if paths.migration_pending.exists():
        pending = _load_json(paths.migration_pending, "pending migration")
        if pending.get("workspace_id") != marker["workspace_id"] or pending.get("version") != 1:
            raise WorkspaceRuntimeError("pending migration identity/version conflicts; preserved")
        entries = pending.get("files")
        if not isinstance(entries, dict):
            raise WorkspaceRuntimeError("pending migration file manifest is invalid; preserved")
        for name, digest in entries.items():
            relative = Path(name)
            if (relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name
                    or name in {"", "."} or not isinstance(digest, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                raise WorkspaceRuntimeError("pending migration file entry is invalid; preserved")
        unexpected = {p.relative_to(paths.legacy_learning_records).as_posix() for p in legacy_files} - set(entries)
        if unexpected:
            raise WorkspaceRuntimeError("legacy files changed during pending migration; preserved")
        sources = [(paths.legacy_learning_records / name, digest) for name, digest in entries.items()]
    else:
        sources = [(source, _sha256(source)) for source in legacy_files]
    for source, source_hash in sources:
        relative = source.relative_to(paths.legacy_learning_records)
        destination = paths.learning_records / relative
        for candidate in (source, destination):
            if any(p.is_symlink() for p in (candidate, *candidate.parents) if p != paths.workspace and paths.workspace in p.parents):
                raise WorkspaceRuntimeError("migration symlink is not allowed; preserved")
        if source.exists() and (not source.is_file() or _sha256(source) != source_hash):
            conflicts.append({"source": str(source), "destination": str(destination)})
            continue
        if not source.exists() and not destination.exists():
            raise WorkspaceRuntimeError("pending migration record missing from both locations; preserved")
        if destination.exists():
            if not destination.is_file() or _sha256(destination) != source_hash:
                conflicts.append({"source": str(source), "destination": str(destination)})
                continue
        planned.append((source, destination, source_hash))

    if conflicts:
        _append_event(
            paths.migration_log,
            {"action": "layout_migration_blocked", "conflicts": conflicts},
        )
        raise WorkspaceRuntimeError(
            "Learning Record migration has conflicting destination files; both layouts were "
            f"preserved unchanged: {conflicts}"
        )

    ensure_directory(paths.learning_records)
    if pending is None:
        pending = {
            "version": 1,
            "workspace_id": marker["workspace_id"],
            "started_at": utc_now(),
            "files": {source.relative_to(paths.legacy_learning_records).as_posix(): digest
                      for source, _, digest in planned},
        }
        atomic_write_json(paths.migration_pending, pending)
    copied: list[str] = []
    try:
        for source, destination, expected_hash in planned:
            if not destination.exists():
                ensure_directory(destination.parent)
                handle, temporary_name = tempfile.mkstemp(
                    prefix=f".{destination.name}.", suffix=".migration.tmp", dir=destination.parent
                )
                os.close(handle)
                temporary = Path(temporary_name)
                try:
                    shutil.copyfile(source, temporary)
                    with temporary.open("rb") as stream:
                        os.fsync(stream.fileno())
                    if _sha256(temporary) != expected_hash:
                        raise WorkspaceRuntimeError(
                            f"Learning Record migration verification failed before install: {source}"
                        )
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                copied.append(str(destination))
            if _sha256(destination) != expected_hash:
                raise WorkspaceRuntimeError(
                    f"Learning Record migration verification failed: {destination}"
                )
    except BaseException:
        _append_event(
            paths.migration_log,
            {"action": "layout_migration_failed", "copied": copied},
        )
        raise

    # The durable manifest survives interruption. Normal readiness stops until
    # this helper verifies/retries the entire set, even if no legacy files remain.
    try:
        for source, destination, expected_hash in planned:
            if _sha256(destination) != expected_hash:
                raise WorkspaceRuntimeError(
                    f"Learning Record changed during migration verification: {destination}"
                )
            if source.exists():
                if _sha256(source) != expected_hash:
                    raise WorkspaceRuntimeError("legacy record changed before retirement; preserved")
                source.unlink()
        if paths.legacy_learning_records.exists():
            for directory in sorted(
                (item for item in paths.legacy_learning_records.rglob("*") if item.is_dir()),
                reverse=True,
            ):
                directory.rmdir()
            paths.legacy_learning_records.rmdir()
        updated = dict(marker)
        updated["layout_version"] = LAYOUT_VERSION
        updated["last_used_at"] = utc_now()
        atomic_write_json(paths.marker, updated)
        _append_event(
            paths.migration_log,
            {"action": "layout_migration_completed", "migrated_files": copied},
        )
        paths.migration_pending.unlink()
    except BaseException as error:
        # Ordinary failure: restore missing legacy files from verified copies.
        # Never overwrite conflicting files. If restoration itself fails, retain
        # both the manifest and the verified visible records for a formal retry.
        restoration_errors = []
        for source, destination, expected_hash in planned:
            try:
                if not source.exists():
                    if _sha256(destination) != expected_hash:
                        raise WorkspaceRuntimeError("visible copy changed; refusing restoration")
                    ensure_directory(source.parent)
                    handle, name = tempfile.mkstemp(prefix=".restore-", dir=source.parent)
                    os.close(handle)
                    temporary = Path(name)
                    try:
                        shutil.copyfile(destination, temporary)
                        with temporary.open("rb") as stream:
                            os.fsync(stream.fileno())
                        if _sha256(temporary) != expected_hash:
                            raise WorkspaceRuntimeError("restored legacy hash mismatch")
                        os.replace(temporary, source)
                    finally:
                        temporary.unlink(missing_ok=True)
            except Exception as restore_error:
                restoration_errors.append(str(restore_error))
        _append_event(
            paths.migration_log,
            {"action": "layout_migration_cleanup_incomplete", "copied": copied,
             "error": str(error), "restoration_errors": restoration_errors,
             "retry": "migrate-layout; normal readiness is blocked by migration-pending.json"},
        )
        raise
    return {
        "status": "migrated",
        "layout_version": LAYOUT_VERSION,
        "migrated_files": copied,
    }


def ensure_workspace(workspace: Path) -> WorkspacePaths:
    """Idempotently create only Daily SRL base infrastructure in an authorized workspace."""
    paths = _validate_workspace_shell(workspace)

    if paths.migration_pending.exists():
        raise WorkspaceRuntimeError(
            "incomplete migration preserved; run the formal migrate-layout helper to verify and retry: "
            f"{paths.migration_pending}"
        )

    root_preexisted = paths.root.exists()
    required_directories = (paths.runtime, paths.structured_records)
    for directory in required_directories:
        if directory.exists() and not directory.is_dir():
            raise WorkspaceRuntimeError(f"required Daily SRL directory is a file: {directory}")
    if root_preexisted and not paths.marker.exists():
        entries = list(paths.root.iterdir())
        known_names = {
            "runtime",
            "structured-records",
            "learning-records",
            "bridge.json",
            "bridge.log",
        }
        has_known_structure = not entries or any(item.name in known_names for item in entries)
        if not has_known_structure:
            raise WorkspaceRuntimeError(
                f"existing {paths.root} is not clearly a Daily SRL workspace; preserving it unchanged"
            )

    ensure_directory(paths.root)
    for directory in required_directories:
        ensure_directory(directory)

    try:
        probe_handle, probe_name = tempfile.mkstemp(prefix=".write-check-", dir=paths.root)
        os.close(probe_handle)
        Path(probe_name).unlink()
    except OSError as error:
        raise WorkspaceRuntimeError(f"workspace is not writable: {paths.root}: {error}") from error

    now = utc_now()
    if paths.marker.exists():
        marker = _validate_marker(_load_json(paths.marker, "workspace marker"), paths.marker)
        if _marker_layout_version(marker) == 1:
            legacy_files = _legacy_files(paths)
            if legacy_files:
                raise WorkspaceRuntimeError(
                    "legacy Learning Records require verified migration before normal use; run "
                    f"workspace_runtime.py --workspace '{paths.workspace}' migrate-layout"
                )
            if paths.legacy_learning_records.exists():
                paths.legacy_learning_records.rmdir()
            marker = dict(marker)
            marker["layout_version"] = LAYOUT_VERSION
        marker = dict(marker)
        marker["last_used_at"] = now
    else:
        legacy_files = _legacy_files(paths)
        if legacy_files:
            marker = {
                "workspace_type": WORKSPACE_TYPE,
                "workspace_version": WORKSPACE_VERSION,
                "layout_version": 1,
                "runtime_version": RUNTIME_VERSION,
                "workspace_id": str(uuid.uuid4()),
                "created_at": now,
                "last_used_at": now,
            }
            atomic_write_json(paths.marker, marker)
            raise WorkspaceRuntimeError(
                "legacy Learning Records were detected and preserved; verified migration is "
                "required before normal use; run "
                f"workspace_runtime.py --workspace '{paths.workspace}' migrate-layout"
            )
        if paths.legacy_learning_records.exists():
            paths.legacy_learning_records.rmdir()
        marker = {
            "workspace_type": WORKSPACE_TYPE,
            "workspace_version": WORKSPACE_VERSION,
            "layout_version": LAYOUT_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "workspace_id": str(uuid.uuid4()),
            "created_at": now,
            "last_used_at": now,
        }
    if paths.learning_records.exists() and not paths.learning_records.is_dir():
        raise WorkspaceRuntimeError(
            f"required Learning Records directory is a file: {paths.learning_records}"
        )
    ensure_directory(paths.learning_records)
    atomic_write_json(paths.marker, marker)
    return paths


def read_workspace_marker(workspace: Path) -> dict[str, Any]:
    paths = workspace_paths(workspace)
    return _validate_marker(_load_json(paths.marker, "workspace marker"), paths.marker)


def pair_paths(workspace: Path, kind: str) -> tuple[Path, Path]:
    if kind not in PAIR_SPECS:
        raise ValueError(f"unsupported runtime pair: {kind}")
    paths = workspace_paths(workspace)
    spec = PAIR_SPECS[kind]
    return paths.runtime / spec["state"], paths.runtime / spec["html"]


def inspect_pair(workspace: Path, kind: str) -> dict[str, Any]:
    state_path, html_path = pair_paths(workspace, kind)
    state_exists = state_path.is_file()
    html_exists = html_path.is_file()
    if not state_exists and not html_exists:
        status = "absent"
    elif state_exists and not html_exists:
        try:
            state = _load_json(state_path, f"{kind} state")
        except WorkspaceRuntimeError:
            status = "corrupt_state"
        else:
            status = "state_only"
    elif html_exists and not state_exists:
        status = "html_only"
    else:
        try:
            state = _load_json(state_path, f"{kind} state")
        except WorkspaceRuntimeError:
            status = "corrupt_state"
        else:
            try:
                html_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                status = "invalid_html"
            else:
                status = "ready"
    return {
        "kind": kind,
        "status": status,
        "state_path": state_path,
        "html_path": html_path,
        "state_exists": state_exists,
        "html_exists": html_exists,
    }


def runtime_summary(workspace: Path) -> dict[str, Any]:
    return {kind: inspect_pair(workspace, kind)["status"] for kind in PAIR_SPECS}


def _state_digest(state: dict[str, Any]) -> str:
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _blank_manifest() -> dict[str, Any]:
    return {"schema_version": 1, "updated_at": utc_now(), "pairs": {}}


def read_manifest(workspace: Path, *, allow_missing: bool = True) -> dict[str, Any]:
    path = workspace_paths(workspace).manifest
    if not path.exists() and allow_missing:
        return _blank_manifest()
    value = _load_json(path, "runtime manifest")
    if value.get("schema_version") != 1 or not isinstance(value.get("pairs"), dict):
        raise WorkspaceRuntimeError(f"runtime manifest is invalid: {path}")
    return value


def page_context(kind: str, state: dict[str, Any], workspace_id: str) -> dict[str, str]:
    """Stable per-generation identity; operational metadata never enters State."""
    flow_date = str(state.get(PAIR_SPECS[kind]["date_field"], ""))
    identity_parts = [workspace_id, kind, flow_date, state.get("generated_at")]
    if kind == "daily-page":
        # The same saved State can be shown by incompatible Review forms.
        # Reject an already-open old form rather than accepting its new meaning
        # for task_note under the unchanged full-state save protocol.
        identity_parts.append(DAILY_PAGE_VIEW)
    identity = json.dumps(identity_parts,
                          ensure_ascii=False, separators=(",", ":"))
    return {"workspace_id": workspace_id, "kind": kind, "date": flow_date,
            "runtime_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()}


def render_html(kind: str, state: dict[str, Any], *, workspace_id: str = "") -> str:
    if kind not in PAIR_SPECS:
        raise ValueError(f"unsupported runtime pair: {kind}")
    spec = PAIR_SPECS[kind]
    template_path = Path(__file__).resolve().parents[1] / "templates" / spec["template"]
    template = template_path.read_text(encoding="utf-8")
    token = spec["token"]
    if template.count(token) != 1:
        raise WorkspaceRuntimeError(f"template must contain exactly one {token}: {template_path}")
    state_json = json.dumps(state, ensure_ascii=False, indent=2).replace("</", "<\\/")
    context_json = json.dumps(page_context(kind, state, workspace_id), ensure_ascii=False).replace("</", "<\\/")
    return template.replace(token, state_json).replace("__SAVE_CONTEXT_JSON__", context_json)


def _manifest_entry(kind: str, state: dict[str, Any], *, user_saved: bool) -> dict[str, Any]:
    spec = PAIR_SPECS[kind]
    flow_date = state.get(spec["date_field"])
    if not isinstance(flow_date, str) or not flow_date:
        raise WorkspaceRuntimeError(f"{kind} state has no valid flow date")
    return {
        "flow_date": flow_date,
        "generated_at": state.get("generated_at"),
        "initial_state_sha256": _state_digest(state),
        "user_saved": user_saved,
        "updated_at": utc_now(),
    }


def write_runtime_pair(
    workspace: Path,
    kind: str,
    state: dict[str, Any],
    *,
    clear_pairs: Iterable[str] = (),
) -> tuple[Path, Path]:
    paths = ensure_workspace(workspace)
    with nonblocking_file_lock(paths.root / "page-save.lock", busy_message="当前页面正在保存，请保留输入后重试。"):
        return _write_runtime_pair(workspace, kind, state, clear_pairs=clear_pairs)


def _write_runtime_pair(
    workspace: Path,
    kind: str,
    state: dict[str, Any],
    *,
    clear_pairs: Iterable[str] = (),
) -> tuple[Path, Path]:
    paths = ensure_workspace(workspace)
    state_path, html_path = pair_paths(paths.workspace, kind)
    manifest = read_manifest(paths.workspace)
    manifest = json.loads(json.dumps(manifest))
    manifest["updated_at"] = utc_now()
    manifest["pairs"][kind] = _manifest_entry(
        kind, state, user_saved=state.get("saved_at") is not None
    )
    removals: list[Path] = []
    for clear_kind in clear_pairs:
        if clear_kind == kind:
            continue
        clear_state, clear_html = pair_paths(paths.workspace, clear_kind)
        removals.extend([clear_state, clear_html])
        manifest["pairs"].pop(clear_kind, None)
    state_text = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    replace_bundle(
        {
            state_path: state_text,
            html_path: render_html(kind, state, workspace_id=read_workspace_marker(paths.workspace)["workspace_id"]),
            paths.manifest: json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        },
        remove=removals,
    )
    if inspect_pair(paths.workspace, kind)["status"] != "ready":
        raise WorkspaceRuntimeError(f"{kind} runtime pair was not created completely")
    return html_path, state_path



def refresh_page_client(workspace: Path, kind: str, state: dict[str, Any]) -> bool:
    """Upgrade only a previous client's HTML; never rewrite State or its manifest.

    Open browser-only edits must be protected before upgrading/reopening a page.
    A fresh client always performs GET before accepting a formal save.
    """
    _, html_path = pair_paths(workspace, kind)
    current = html_path.read_text(encoding="utf-8")
    if ('data-daily-srl-client="2"' in current and (
            kind != "daily-page" or f'data-daily-srl-view="{DAILY_PAGE_VIEW}"' in current)):
        return False
    marker = read_workspace_marker(workspace)
    atomic_write_text(html_path, render_html(kind, state, workspace_id=marker["workspace_id"]))
    return True

def mark_runtime_user_saved(workspace: Path, kind: str) -> None:
    paths = workspace_paths(workspace)
    state_path, _ = pair_paths(workspace, kind)
    state = _load_json(state_path, f"{kind} state")
    manifest = read_manifest(workspace)
    manifest = json.loads(json.dumps(manifest))
    current = manifest["pairs"].get(kind)
    if not isinstance(current, dict):
        current = _manifest_entry(kind, state, user_saved=True)
    else:
        current = dict(current)
        current["user_saved"] = True
        current["updated_at"] = utc_now()
    manifest["pairs"][kind] = current
    manifest["updated_at"] = utc_now()
    atomic_write_json(paths.manifest, manifest)


def _extract_initial_state(kind: str, html: str) -> dict[str, Any]:
    script_id = re.escape(PAIR_SPECS[kind]["script_id"])
    pattern = re.compile(
        rf'<script\s+id="{script_id}"\s+type="application/json">(.*?)</script>', re.DOTALL
    )
    match = pattern.search(html)
    if not match:
        raise WorkspaceRuntimeError(f"{kind} HTML does not contain a recoverable initial state")
    try:
        value = json.loads(match.group(1).replace("<\\/", "</"))
    except json.JSONDecodeError as error:
        raise WorkspaceRuntimeError(f"{kind} HTML contains damaged initial state: {error}") from error
    if not isinstance(value, dict):
        raise WorkspaceRuntimeError(f"{kind} HTML initial state must be an object")
    return value


def recover_pair(
    workspace: Path,
    kind: str,
    validator: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Recover only cases with a safe, evidence-backed source of truth."""
    paths = ensure_workspace(workspace)
    info = inspect_pair(paths.workspace, kind)
    status = info["status"]
    state_path: Path = info["state_path"]
    html_path: Path = info["html_path"]
    if status == "ready":
        state = validator(_load_json(state_path, f"{kind} state"))
        return {"status": "ready", "recovered": False, "state": state}
    if status == "absent":
        return {"status": "absent", "recovered": False}
    if status == "corrupt_state":
        _append_event(
            paths.recovery_log,
            {"kind": kind, "action": "blocked_corrupt_state", "state_path": str(state_path)},
        )
        raise WorkspaceRuntimeError(
            f"{kind} State is damaged; it was preserved and must not be overwritten: {state_path}"
        )
    if status in {"state_only", "invalid_html"}:
        state = validator(_load_json(state_path, f"{kind} state"))
        manifest = read_manifest(paths.workspace)
        if not isinstance(manifest["pairs"].get(kind), dict):
            manifest["pairs"][kind] = _manifest_entry(
                kind, state, user_saved=state.get("saved_at") is not None
            )
        manifest["updated_at"] = utc_now()
        replace_bundle(
            {
                html_path: render_html(kind, state, workspace_id=read_workspace_marker(paths.workspace)["workspace_id"]),
                paths.manifest: json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            }
        )
        _append_event(
            paths.recovery_log,
            {"kind": kind, "action": "rebuilt_html_from_state", "html_path": str(html_path)},
        )
        return {"status": "ready", "recovered": True, "source": "state", "state": state}
    if status == "html_only":
        try:
            manifest = read_manifest(paths.workspace, allow_missing=False)
        except WorkspaceRuntimeError:
            _append_event(
                paths.recovery_log,
                {"kind": kind, "action": "blocked_html_only", "reason": "manifest_missing_or_invalid"},
            )
            raise
        entry = manifest["pairs"].get(kind)
        if not isinstance(entry, dict) or entry.get("user_saved") is not False:
            _append_event(
                paths.recovery_log,
                {"kind": kind, "action": "blocked_html_only", "reason": "interaction_possible"},
            )
            raise WorkspaceRuntimeError(
                f"{kind} Runtime is incomplete: HTML exists but State is missing; "
                "a prior user save cannot be ruled out, so the HTML was preserved unchanged"
            )
        html = html_path.read_text(encoding="utf-8")
        state = validator(_extract_initial_state(kind, html))
        if _state_digest(state) != entry.get("initial_state_sha256"):
            _append_event(
                paths.recovery_log,
                {"kind": kind, "action": "blocked_html_only", "reason": "initial_hash_mismatch"},
            )
            raise WorkspaceRuntimeError(
                f"{kind} Runtime is incomplete and its HTML does not match the recorded initial State"
            )
        atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        _append_event(
            paths.recovery_log,
            {"kind": kind, "action": "restored_proven_initial_state", "state_path": str(state_path)},
        )
        return {
            "status": "ready",
            "recovered": True,
            "source": "proven_initial_html",
            "state": state,
        }
    raise WorkspaceRuntimeError(f"{kind} Runtime is incomplete and cannot be recovered safely")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("command", choices=("ensure", "inspect", "paths", "migrate-layout"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "migrate-layout":
            result = migrate_workspace_layout(args.workspace)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        paths = ensure_workspace(args.workspace)
        if args.command == "ensure":
            result = {
                "workspace_root": str(paths.workspace),
                "daily_srl_root": str(paths.root),
                "workspace": read_workspace_marker(paths.workspace),
            }
        elif args.command == "inspect":
            result = {
                "workspace_root": str(paths.workspace),
                "runtime": runtime_summary(paths.workspace),
            }
        else:
            result = {
                "workspace_root": str(paths.workspace),
                "internal_root": str(paths.root),
                "runtime_root": str(paths.runtime),
                "structured_records_root": str(paths.structured_records),
                "learning_records_root": str(paths.learning_records),
                "workspace_metadata_path": str(paths.marker),
                "bridge_metadata_path": str(paths.bridge_metadata),
            }
    except (OSError, WorkspaceRuntimeError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
