#!/usr/bin/env python3
"""Execute one registered delivery through WorkBuddy's official library scripts.

The host supplies its enabled ``library`` 0.6.x directory. No account discovery,
credentials, replacement API or hidden fallback is used here. The delivery
controller owns target selection, current metadata checks and write registration;
this adapter preserves that plan and parses the actual native stdout receipt.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workbuddy_library import LibraryClient, LibraryError, text_id

DRIVE_MARKER = "KS_DRIVE_UPLOAD_OK "
DOC_MARKER = "KS_DOC_CREATE\t"
SCRIPTS = {"drive": "drive/upload_drive_file.py", "doc": "doc/create_doc.py"}


def run_native(argv, **kwargs):
    """Keep native Python replies UTF-8 even on legacy Windows code pages.

    Preserve the host's IPC/authentication environment; only text encoding and
    bytecode output are set. No environment values are inspected or returned.
    """
    environment = dict(os.environ if kwargs.get("env") is None else kwargs["env"])
    environment.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    kwargs["env"] = environment
    return subprocess.run(argv, **kwargs)


def _plan(plan, *, registered=False):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise LibraryError("a schema version 1 delivery plan is required")
    kind = plan.get("node_kind")
    if {"learning": "doc", "structured": "drive"}.get(plan.get("kind")) != kind:
        raise LibraryError("delivery artifact and native node kind do not match")
    if plan.get("operation") not in {"create", "update"}:
        raise LibraryError("only explicit create or update plans are supported")
    for field in ("space_id", "filename"):
        text_id(plan.get(field), field)
    filename = plan["filename"]
    extension = ".md" if kind == "doc" else ".json"
    if (Path(filename).name != filename or "/" in filename or "\\" in filename
            or Path(filename).suffix != extension):
        raise LibraryError("delivery filename must keep its local basename and artifact extension")
    if registered:
        text_id(plan.get("action_id"), "action_id")
    if plan["operation"] == "update":
        node_id = text_id(plan.get("id"), "id")
        version = plan.get("expected_version")
        if (type(version) is not int
                and not (isinstance(version, str) and version.strip()
                         and not any(ord(c) < 32 for c in version))):
            raise LibraryError("an update requires the registered remote revision")
        if kind == "doc" and plan.get("confirm_overwrite") is not True:
            raise LibraryError("a registered document replacement must be explicitly authorized")
        alias = "node_block_id" if kind == "doc" else "node_id"
        if alias in plan and plan[alias] != node_id:
            raise LibraryError("update identifiers must identify the same registered node")
    elif any(plan.get(key) is not None for key in ("id", "node_id", "node_block_id", "expected_version")):
        raise LibraryError("a create plan must not carry an existing node target")
    return kind


def _stdout(raw):
    if isinstance(raw, str):
        return raw
    if getattr(raw, "returncode", None) != 0 or not isinstance(getattr(raw, "stdout", None), str):
        raise LibraryError("official library write process failed; retain the registered action")
    return raw.stdout


def normalize_receipt(plan, raw):
    """Normalize only the official success markers; never invent remote facts.

    Local context is copied from the registered plan. Native scripts do not emit
    a revision, so no revision is fabricated; node-info supplies that evidence.
    A document's nonzero content error counts are retained for the controller's
    uncertain-write handling, rather than interpreted as complete success.
    """
    kind = _plan(plan, registered=True)
    stdout = _stdout(raw)
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise LibraryError("official library write returned no receipt; do not retry blindly")
    markers = [line for line in lines if line.startswith((DRIVE_MARKER, DOC_MARKER))]
    if len(markers) != 1:
        raise LibraryError("official library write did not return one unambiguous receipt")
    # Native scripts can report an error with exit status zero. A mixed success
    # and error response is uncertain, never a second invitation to upload.
    replies = 0
    details = None
    for line in lines:
        if line.startswith((DRIVE_MARKER, DOC_MARKER)):
            continue
        if line.startswith("KS_USER_REPLY\t"):
            replies += 1
            if replies > 1:
                raise LibraryError("official library write returned ambiguous user replies")
            continue
        if line.lstrip().startswith("{"):
            try:
                value = json.loads(line)
            except (TypeError, ValueError) as exc:
                raise LibraryError("official library write returned malformed evidence") from exc
            if not isinstance(value, dict) or "error" in value:
                raise LibraryError("official library write reported an error; inspect the registered action")
            if (kind != "doc" or set(value) != {"failedCmdIds", "fatalCmdIds"}
                    or details is not None or any(not isinstance(value[key], list) for key in value)
                    or any(not isinstance(item, str) or not item.strip()
                           for group in value.values() for item in group)):
                raise LibraryError("official library write returned an unsupported evidence shape")
            details = value
            continue
        raise LibraryError("official library write returned unsupported output; retain the action")
    line = markers[0]
    receipt = {key: plan[key] for key in ("action_id", "operation", "space_id", "filename", "node_kind")}
    receipt.update(schema_version=1)
    if kind == "drive":
        if not line.startswith(DRIVE_MARKER):
            raise LibraryError("native receipt kind differs from the registered artifact")
        try:
            native = json.loads(line[len(DRIVE_MARKER):])
        except (TypeError, ValueError) as exc:
            raise LibraryError("official drive receipt is malformed") from exc
        if (not isinstance(native, dict) or "error" in native or native.get("file_name") != plan["filename"]
                or native.get("ext") != "json"):
            raise LibraryError("official drive receipt does not preserve the registered filename")
        receipt.update(id=text_id(native.get("node_block_id"), "native node id"), success=True,
                       evidence_ref="official-library:drive/upload_drive_file.py:KS_DRIVE_UPLOAD_OK")
        url = native.get("url")
    else:
        if not line.startswith(DOC_MARKER):
            raise LibraryError("native receipt kind differs from the registered artifact")
        columns = line.split("\t")
        if len(columns) != 6 or columns[2] != "doc":
            raise LibraryError("official document receipt is incomplete or has another node kind")
        counts = columns[4:6]
        if any(not value.isascii() or not value.isdecimal() for value in counts):
            raise LibraryError("official document receipt is missing exact content error counts")
        failed, fatal = map(int, counts)
        if details is not None and (len(details["failedCmdIds"]), len(details["fatalCmdIds"])) != (failed, fatal):
            raise LibraryError("official document receipt contains inconsistent content error counts")
        receipt.update(id=text_id(columns[1], "native node id"), success=failed == fatal == 0,
                       failedCount=failed, fatalCount=fatal,
                       evidence_ref="official-library:doc/create_doc.py:KS_DOC_CREATE")
        url = columns[3]
    if plan["operation"] == "update" and receipt["id"] != plan["id"]:
        raise LibraryError("official replacement receipt returned a different node; retain the action")
    if url:
        receipt["url"] = text_id(url, "native node URL")
    return receipt


class LibraryWriter(LibraryClient):
    """Exact native create/update argv with an injectable runner for offline QA."""

    def __init__(self, library_dir, *, runner=None, python=None):
        super().__init__(library_dir, runner=runner or run_native, python=python)

    def preflight(self, plan):
        """Check local official contract and input before begin_transfer; no calls."""
        kind = _plan(plan)
        try:
            path = Path(plan["file"])
        except (KeyError, TypeError) as exc:
            raise LibraryError("delivery needs its absolute local snapshot path") from exc
        if not path.is_absolute() or not path.is_file() or path.name != plan["filename"]:
            raise LibraryError("delivery needs the exact existing local snapshot basename")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise LibraryError("cannot inspect the registered delivery snapshot") from exc
        limit = (50 if kind == "doc" else 100) * 1024 * 1024
        if not 0 < size <= limit:
            raise LibraryError("registered delivery snapshot is empty or exceeds the official limit")
        relative = SCRIPTS[kind]
        argv = self._argv(relative)
        try:
            tree = ast.parse(Path(argv[1]).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise LibraryError("cannot validate the supplied official write contract") from exc
        arguments = {arg.value for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"
                     for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}
        constants = {node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        needed = ({"path", "--file-name", "--space-id", "--node-id"} if kind == "drive" else
                  {"--content-file", "--title", "--space-id", "--node-block-id", "--confirm-overwrite"})
        marker = DRIVE_MARKER if kind == "drive" else DOC_MARKER
        if not needed.issubset(arguments) or not any(marker in value for value in constants):
            raise LibraryError("enabled official script does not implement the supported write contract")
        if kind == "drive":
            argv += [str(path), "--space-id", plan["space_id"], "--file-name", plan["filename"]]
            if plan["operation"] == "update":
                argv += ["--node-id", plan["id"]]
        else:
            argv += ["--content-file", str(path), "--space-id", plan["space_id"]]
            if plan["operation"] == "update":
                # The native overwrite preserves the existing title. Do not
                # accidentally rename a registered mirror during its update.
                argv += ["--node-block-id", plan["id"], "--confirm-overwrite"]
            else:
                argv += ["--title", plan["filename"]]
        return argv

    def write(self, plan):
        """Invoke exactly once; ambiguous/error results must never trigger retry."""
        _plan(plan, registered=True)
        argv = self.preflight(plan)
        try:
            result = self.runner(argv, capture_output=True, text=True, encoding="utf-8", check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise LibraryError("official library write did not complete; retain the registered action") from exc
        return normalize_receipt(plan, result)
