#!/usr/bin/env python3
"""Read-only adapter for WorkBuddy's documented native library skill.

The host must supply its enabled official ``library`` skill directory. This
module neither searches installed apps nor reads account caches or credentials.
Only discover/list perform native read calls. Plan commands return argv without
executing them; uploads remain the host's responsibility under its current tool
schema, including explicit resolution of a team-space destination.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

PROFILE = "workbuddy-library-v0.6"
LIGHT_PROFILE = "workbuddy-library-light-v1"
MAX_PAGES = 1000
TRANSIENT = {"50000", "56038", "500", "HTTP_500", "502", "HTTP_502",
             "503", "HTTP_503", "504", "HTTP_504", "NETWORK_ERROR",
             "TEMPORARY_ERROR", "INVALID_RESPONSE", "UNKNOWN"}
WRITABLE = {"owner", "admin", "editor"}
READ_ONLY = {"reader", "invalid"}
CAPABILITY_SCRIPTS = {"list_spaces": "space_api.py", "lookup_files": "space_api.py",
                      "create_file": "drive/upload_drive_file.py",
                      "read_document": "doc/get_doc_reviews.py",
                      "download_file": "drive/get_download_link.py"}


class LibraryError(ValueError):
    """Safe failure text: never includes native responses, URLs or credentials."""


def text_id(value, label):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise LibraryError(f"{label} must be a nonempty identifier")
    return value


def validate_library_dir(library_dir):
    """Validate only the explicit path supplied by the host, without scanning."""
    root = Path(library_dir).expanduser().resolve()
    manifest = root / "SKILL.md"
    api = root / "space_api.py"
    if not root.is_dir() or not manifest.is_file() or not api.is_file():
        raise LibraryError("the supplied official library directory is incomplete")
    try:
        raw = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LibraryError("cannot read the supplied library manifest") from exc
    header = re.match(r"\A---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|\Z)", raw, re.S)
    if not header or not re.search(r"(?m)^name:\s*['\"]?library['\"]?\s*$", header[1]):
        raise LibraryError("the supplied skill is not named library")
    version = re.search(r"(?m)^version:\s*['\"]?([^\s'\"]+)['\"]?\s*$", header[1])
    if not version or not re.fullmatch(r"0\.6\.[0-9]+", version[1]):
        raise LibraryError("this adapter requires the documented library 0.6 contract")
    return root


class LibraryClient:
    """Narrow read-only command allowlist; runner is injectable for offline QA."""
    def __init__(self, library_dir, *, runner=None, python=None):
        self.root = validate_library_dir(library_dir)
        self.runner = runner or subprocess.run
        self.python = str(python or sys.executable)

    def _argv(self, relative_script, *arguments):
        path = self.root / relative_script
        if not path.is_file() or not path.resolve().is_relative_to(self.root):
            raise LibraryError("a documented library script is unavailable")
        return [self.python, str(path), *map(str, arguments)]

    def plan(self, operation, *, node_id=None):
        """Return exact documented read argv; never invoke a service."""
        node_id = text_id(node_id, "node_id")
        if operation == "node-info":
            return self._argv("space_api.py", "space.workspace.node-info", "--node-id", node_id)
        if operation == "download-file":
            return self._argv("drive/get_download_link.py", "--node-id", node_id)
        if operation == "read-document":
            return self._argv("doc/get_doc_reviews.py", "--page-id", node_id)
        raise LibraryError("unsupported read-only operation")

    def drive_upload_plan(self, path, filename, space_id):
        """Plan the official new-drive upload, never execute a remote mutation.

        The host must apply its native mutation/permission/latest-state rules
        before running this plan. A space target is passed as --space-id, never
        repurposed as a parent node. No --node-id means a new immutable file.
        """
        path = Path(path)
        filename = text_id(filename, "filename")
        if (not path.is_absolute() or not path.is_file()
                or Path(filename).name != filename or "/" in filename or "\\" in filename
                or path.suffix.lower() != Path(filename).suffix.lower()):
            raise LibraryError("upload plan needs an absolute file and a matching display extension")
        return self._argv("drive/upload_drive_file.py", str(path),
                          "--space-id", text_id(space_id, "space_id"), "--file-name", filename)

    def _query(self, method, *arguments):
        if method not in {"space.workspace.list-user-spaces", "space.workspace.list-nodes", "space.workspace.node-info"}:
            raise LibraryError("native method is outside this adapter's read allowlist")
        argv = self._argv("space_api.py", method, *arguments)
        for attempt in range(2):
            try:
                result = self.runner(argv, capture_output=True, text=True, encoding="utf-8",
                                     timeout=30, check=False)
            except subprocess.TimeoutExpired as exc:
                if attempt == 0:
                    continue
                raise LibraryError("native read timed out; retain local records") from exc
            except OSError as exc:
                raise LibraryError("cannot run the official library read command") from exc
            stdout = result.stdout or ""
            if not stdout.strip() and result.returncode != 0 and attempt == 0:
                continue
            try:
                response = json.loads(stdout)
            except (ValueError, TypeError) as exc:
                raise LibraryError("native read did not return valid JSON evidence") from exc
            if not isinstance(response, dict):
                raise LibraryError("native read did not return an object")
            if "error" in response:
                # Only explicitly documented transient errors can be retried.
                # Unknown/auth/consent/permission errors never trigger guessing.
                codes = re.findall(r"\bcode=([A-Za-z0-9_]+)", str(response["error"]))
                code = codes[-1] if codes else None
                if code in TRANSIENT and attempt == 0:
                    continue
                raise LibraryError("official library read failed; inspect the host's native error handling")
            if result.returncode != 0:
                raise LibraryError("official library read process failed")
            if not isinstance(response.get("data"), dict):
                raise LibraryError("native read is missing data evidence")
            return response["data"]
        raise LibraryError("native read did not complete")

    def _pages(self, method, collection, *arguments):
        rows = []
        seen = set()
        offset = 0
        for _ in range(MAX_PAGES):
            data = self._query(method, *arguments, "--limit", "100", "--offset", str(offset))
            page = data.get(collection)
            if not isinstance(page, list) or type(data.get("hasMore")) is not bool:
                raise LibraryError("incomplete native pagination evidence")
            for row in page:
                if not isinstance(row, dict):
                    raise LibraryError("invalid native list item")
                identifier = text_id(row.get("spaceId" if collection == "spaces" else "id"), "native id")
                if identifier in seen:
                    raise LibraryError("duplicate native ID; pagination must be reconciled")
                seen.add(identifier)
                rows.append(row)
            if data["hasMore"] is False:
                return rows
            current, limit = data.get("offset"), data.get("limit")
            if type(current) is not int or type(limit) is not int or current != offset or not 1 <= limit <= 100:
                raise LibraryError("invalid native pagination cursor")
            if not page:
                raise LibraryError("native pagination has no progress")
            offset = current + limit
        raise LibraryError("native pagination exceeded the bounded read limit")

    def list_spaces(self):
        return self._pages("space.workspace.list-user-spaces", "spaces")

    def list_nodes(self, space_id, *, parent_node_id=None):
        arguments = ["--space-id", text_id(space_id, "space_id")]
        if parent_node_id is not None:
            arguments += ["--parent-node-id", text_id(parent_node_id, "parent_node_id")]
        return self._pages("space.workspace.list-nodes", "nodes", *arguments)

    @staticmethod
    def delivery_node(row, space_id=None):
        """Metadata only. The official title may omit a file extension."""
        if not isinstance(row, dict):
            raise LibraryError("invalid native node metadata")
        title = row.get("title")
        if not isinstance(title, str) or any(ord(c) < 32 for c in title):
            raise LibraryError("invalid native node title")
        return dict(id=text_id(row.get("id"), "node.id"),
                    space_id=text_id(row.get("spaceId", space_id), "node.spaceId"),
                    filename=title, node_kind=text_id(row.get("kind"), "node.kind"),
                    parent_id=row.get("parentId"), version=row.get("version"))

    def node_info(self, node_id):
        node_id = text_id(node_id, "node_id")
        data = self._query("space.workspace.node-info", "--node-id", node_id)
        node = self.delivery_node(data.get("node"))
        if node["id"] != node_id:
            raise LibraryError("node metadata belongs to a different node")
        return dict(schema_version=1, exists=True,
                    evidence_ref="official-library:space.workspace.node-info", **node)

    def discover_host(self, session_id, *, account_id=None, light=False):
        """Normalize actual official fields; no native raw payload enters hostfile."""
        session_id = text_id(session_id, "session_id")
        if account_id is not None:
            account_id = text_id(account_id, "account_id")
        spaces = []
        for row in self.list_spaces():
            category = row.get("category")
            if category not in {"personal", "team"}:
                raise LibraryError("unknown native space category")
            role = row.get("role")
            if not isinstance(role, str):
                role = None
            can_write = True if role in WRITABLE else False if role in READ_ONLY else None
            title = row.get("title")
            if not isinstance(title, str) or any(ord(c) < 32 for c in title):
                raise LibraryError("invalid native space title")
            spaces.append(dict(id=text_id(row.get("spaceId"), "spaceId"),
                               name=title,
                               category=category, role=role, can_write=can_write))
        capabilities = [name for name, relative in CAPABILITY_SCRIPTS.items()
                        if (self.root / relative).is_file()
                        and (self.root / relative).resolve().is_relative_to(self.root)]
        if light:
            capabilities = ["list_spaces", "lookup_files", "inspect_file"]
            writes = [self.root / name for name in ("drive/upload_drive_file.py", "doc/create_doc.py")]
            if all(path.is_file() and path.resolve().is_relative_to(self.root) for path in writes):
                capabilities += ["create_file", "update_file"]
        return dict(schema_version=1, host="workbuddy", profile=LIGHT_PROFILE if light else PROFILE,
                    host_evidence="official-library-skill:validated-name-and-version",
                    session_id=session_id, account_id=account_id, query_ok=True, complete=True,
                    checked_at=datetime.now(timezone.utc).isoformat(),
                    capabilities=capabilities,
                    capability_discovery=dict(complete=True, library_skill="loaded",
                                              evidence_ref="official-library:SKILL.md+manage/entry.md"),
                    list_evidence="official-library:space.workspace.list-user-spaces:all-pages",
                    upload_target_resolution="runtime_schema_required",
                    spaces=spaces)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--account-id")
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover")
    discover.add_argument("--light", action="store_true", help="RC4 metadata confirmation; no body-read capabilities required")
    discover.add_argument("--host-file", type=Path,
                          default=Path(".daily-srl/sync/workbuddy-host.json"))
    nodes = commands.add_parser("list-nodes")
    nodes.add_argument("--space-id", required=True)
    nodes.add_argument("--parent-node-id")
    nodes.add_argument("--light", action="store_true")
    info = commands.add_parser("node-info")
    info.add_argument("--node-id", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("operation", choices=("node-info", "download-file", "read-document"))
    plan.add_argument("--node-id", required=True)
    args = parser.parse_args(argv)
    try:
        client = LibraryClient(args.library_dir)
        if args.command == "discover":
            import team_space_sync as sync
            workspace, _ = sync.root_for(args.workspace)
            host_path = sync.safe_path(workspace, args.host_file)
            host = client.discover_host(args.session_id, account_id=args.account_id, light=args.light)
            sync.atomic(host_path, sync.encoded(host))
            output = dict(status="discovered", host_file=str(host_path),
                          spaces=len(host["spaces"]), profile=host["profile"])
        elif args.command == "list-nodes":
            rows = client.list_nodes(args.space_id, parent_node_id=args.parent_node_id)
            # Preserve only identity/target fields needed for structural lookup.
            output = dict(schema_version=1, space_id=args.space_id,
                          parent_node_id=args.parent_node_id, lookup_complete=True,
                          nodes=[{key: row[key] for key in ("id", "title", "kind", "spaceId", "parentId", "version")
                                  if key in row} for row in rows])
            if args.light:
                output.update(evidence_ref="official-library:space.workspace.list-nodes:all-pages",
                              nodes=[client.delivery_node(row, args.space_id) for row in rows])
        elif args.command == "node-info":
            output = client.node_info(args.node_id)
        else:
            output = dict(operation=args.operation,
                          argv=client.plan(args.operation, node_id=args.node_id),
                          executed=False,
                          prerequisite="node-info must confirm kind and actual space before content read")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (LibraryError, OSError, ValueError, RuntimeError) as exc:
        # Avoid echoing OSError paths, native payloads or host error URLs.
        reason = str(exc) if isinstance(exc, LibraryError) else "local adapter validation failed"
        print(json.dumps(dict(status="error", error=reason, local_records_untouched=True)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
