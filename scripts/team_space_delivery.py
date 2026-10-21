#!/usr/bin/env python3
"""Light WorkBuddy delivery protocol; the host performs official library calls.

Inputs are normalized adapter evidence, not vendor API request schemas. Local
final records retain their integrity checks. Cloud confirmation uses a successful
write receipt and node metadata only, without downloading or reading file bodies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import team_space_sync as common
import support_events as support
from workspace_runtime import ensure_directory, WorkspaceRuntimeError

PROFILE = "workbuddy-library-light-v1"
KINDS = common.KINDS
NODE_KINDS = {"learning": "doc", "structured": "drive"}
CONFIRMATION = "upload_receipt_and_metadata"


def load_context(workspace, root, token):
    context = common.read_json(workspace, root / "context.json")
    if (not isinstance(context, dict) or context.get("schema_version") != 1
            or context.get("token") != token or context.get("profile") != PROFILE):
        raise ValueError("current light delivery context required; discover current host again")
    return context


def discover(workspace, host):
    if not isinstance(host, dict):
        raise ValueError("host must be an object")
    host = dict(host)
    if host.get("profile", PROFILE) != PROFILE:
        raise ValueError("light delivery requires its documented library profile")
    host["profile"] = PROFILE
    return common.discover(workspace, host)


def select(workspace, token, space_id):
    with common.locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        if context["status"] != "needs_selection":
            raise ValueError("no unresolved multiple-space choice")
        chosen = next((s for s in context["matches"] if s["id"] == space_id), None)
        if chosen is None:
            raise ValueError("selected space must occur in current complete enumeration")
        context["selected"] = chosen
        context["status"] = common.ready_status(context)
        common.atomic(root / "context.json", common.encoded(context))
        return context


def scope_for(context, day):
    return common.sha(common.encoded(dict(account_id=context["account_id"],
                       session_id=context["session_id"] if context["account_id"] is None else None,
                       space_id=context["selected"]["id"], record_date=day)))


def catalog(workspace, root):
    directory = common.safe_path(workspace, root / "deliveries")
    ensure_directory(directory)
    path = directory / "catalog.json"
    value = common.read_json(workspace, path) if path.exists() else dict(schema_version=1, scopes={})
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("scopes"), dict):
        raise ValueError("invalid delivery catalog; retain it for diagnosis")
    return path, value


def put_catalog(path, value):
    common.atomic(path, common.encoded(value))


def artifact_names(day):
    return {"learning": f"{day}.md", "structured": f"{day}.json"}


def identity(scope, version):
    return common.sha(common.encoded(dict(scope=scope, version=version)))


def persist(folder, job, token):
    states = [a["state"] for a in job["artifacts"].values()]
    if all(a["state"] == "confirmed" and a.get("confirmed_token") == token for a in job["artifacts"].values()):
        job["status"] = "synced"
        job["confirmation_level"] = CONFIRMATION
    elif "conflict" in states:
        job["status"] = "conflict"
    elif "uncertain" in states:
        job["status"] = "uncertain"
    elif any(s in ("uploading", "awaiting_confirmation", "confirmed") for s in states):
        job["status"] = "awaiting_confirmation"
    else:
        job["status"] = "pending"
    job["updated_at"] = common.stamp()
    common.atomic(folder / "job.json", common.encoded(job))


def load_job(workspace, root, job_id, context, *, verify_payload=True):
    if not isinstance(job_id, str) or not common.HEX.fullmatch(job_id):
        raise ValueError("invalid delivery job ID")
    folder = common.safe_path(workspace, root / "deliveries" / job_id)
    job = common.read_json(workspace, folder / "job.json")
    if (not isinstance(job, dict) or job.get("schema_version") != 1
            or job.get("profile") != PROFILE or job.get("job_id") != job_id):
        raise ValueError("invalid delivery manifest")
    day = support.valid_date(job["record_date"])
    if (not context.get("selected") or job.get("scope") != scope_for(context, day)
            or job.get("space_id") != context["selected"]["id"]
            or job.get("account_id") != context["account_id"]
            or (context["account_id"] is None and job.get("session_id") != context["session_id"])):
        raise ValueError("delivery belongs to another account, session or space")
    if (not isinstance(job.get("version"), str) or not common.HEX.fullmatch(job["version"])
            or identity(job["scope"], job["version"]) != job_id):
        raise ValueError("invalid delivery identity")
    names = artifact_names(day)
    digest = {}
    for kind in KINDS:
        item = job["artifacts"][kind]
        if item["filename"] != names[kind] or item["node_kind"] != NODE_KINDS[kind]:
            raise ValueError("delivery payload name/type changed")
        if (not isinstance(item.get("sha256"), str) or not common.HEX.fullmatch(item["sha256"])
                or type(item.get("size")) is not int or item["size"] < 0):
            raise ValueError("invalid frozen payload metadata")
        digest[kind] = item["sha256"]
        if verify_payload:
            data = common.safe_path(workspace, folder / names[kind]).read_bytes()
            if common.sha(data) != item["sha256"] or len(data) != item["size"]:
                raise ValueError("frozen payload changed; do not transfer")
    if common.sha(common.encoded(digest)) != job["version"]:
        raise ValueError("frozen record pair changed")
    catalog_path, index = catalog(workspace, root)
    entry = index["scopes"].get(job["scope"])
    if not entry or entry.get("latest_job_id") != job_id:
        raise ValueError("delivery superseded by a newer final record; never overwrite it with an old job")
    return folder, job, catalog_path, index, entry


def prepare(workspace, token, day):
    day = support.valid_date(day)
    with support.day_lock(Path(workspace), day) as (paths, targets):
        record = support.read_pair(targets, support.load_journal(Path(workspace), day), day)
        if record is None or record.get("record_status") != "final":
            raise ValueError("only an integrity-checked final record pair may be delivered")
        payloads = {"learning": targets[2].read_bytes(), "structured": targets[1].read_bytes()}
    with common.locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        if not context.get("selected"):
            raise ValueError("no selected learning space")
        scope = scope_for(context, day)
        version = common.sha(common.encoded({k: common.sha(v) for k, v in payloads.items()}))
        job_id = identity(scope, version)
        catalog_path, index = catalog(workspace, root)
        entry = index["scopes"].setdefault(scope, dict(receipts={}))
        previous_id = entry.get("latest_job_id")
        if previous_id and previous_id != job_id:
            old_path = common.safe_path(workspace, root / "deliveries" / previous_id / "job.json")
            old = common.read_json(workspace, old_path)
            if any(a["state"] in ("uploading", "uncertain", "awaiting_confirmation") for a in old["artifacts"].values()):
                raise ValueError("finish or diagnose the previous pending write before replacing this day's mirror")
            old["status"] = "superseded"
            common.atomic(old_path, common.encoded(old))
        folder = common.safe_path(workspace, root / "deliveries" / job_id)
        ensure_directory(folder)
        if (folder / "job.json").exists():
            entry["latest_job_id"] = job_id
            put_catalog(catalog_path, index)
            _, job, _, _, _ = load_job(workspace, root, job_id, context)
        else:
            items = {}
            for kind, data in payloads.items():
                name = artifact_names(day)[kind]
                target = common.safe_path(workspace, folder / name)
                if target.exists() and target.read_bytes() != data:
                    raise ValueError("interrupted delivery snapshot conflicts")
                common.atomic(target, data)
                items[kind] = dict(filename=name, node_kind=NODE_KINDS[kind], sha256=common.sha(data),
                                   size=len(data), state="unchecked")
            job = dict(schema_version=1, profile=PROFILE, job_id=job_id, scope=scope, version=version,
                       account_id=context["account_id"], session_id=context["session_id"],
                       space_id=context["selected"]["id"], record_date=day,
                       created_at=common.stamp(), artifacts=items)
            entry["latest_job_id"] = job_id
            put_catalog(catalog_path, index)
        persist(folder, job, token)
        return dict(job_id=job_id, status=job["status"], target=context["selected"],
                    readiness=context["status"], artifacts=job["artifacts"],
                    confirmation_level=CONFIRMATION)


def evidence_object(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("evidence requires schema_version 1")
    common.required(value.get("evidence_ref"), "evidence_ref")


def matches_name(item, artifact):
    return item.get("filename") in (artifact["filename"], Path(artifact["filename"]).stem)


def revision(value):
    if value is None or type(value) is int:
        return value
    if isinstance(value, str):
        return common.required(value, "version")
    raise ValueError("version must be an integer, a nonempty opaque string or null")


def inspect(workspace, token, job_id, evidence):
    """Consume one complete directory listing for both local artifacts."""
    evidence_object(evidence)
    if evidence.get("lookup_complete") is not True or not isinstance(evidence.get("nodes"), list):
        raise ValueError("complete paginated directory evidence required")
    with common.locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        folder, job, _, _, entry = load_job(workspace, root, job_id, context)
        if evidence.get("space_id") != job["space_id"]:
            raise ValueError("directory belongs to another space")
        ids = set()
        for node in evidence["nodes"]:
            if not isinstance(node, dict) or node.get("space_id") != job["space_id"]:
                raise ValueError("directory node belongs to another space")
            node_id = common.required(node.get("id"), "node.id")
            common.required(node.get("filename"), "node.filename")
            common.required(node.get("node_kind"), "node.node_kind")
            revision(node.get("version"))
            if node_id in ids:
                raise ValueError("duplicate directory ID; reconcile pagination")
            ids.add(node_id)
        actions = {}
        for kind in KINDS:
            artifact = job["artifacts"][kind]
            if artifact["state"] in ("uploading", "uncertain"):
                actions[kind] = "stop_uncertain_write"
                continue
            nodes = [n for n in evidence["nodes"]
                     if n.get("node_kind") == artifact["node_kind"] and matches_name(n, artifact)]
            registered = entry["receipts"].get(kind)
            if artifact.get("receipt"):
                # Reusing this same complete listing must not undo metadata
                # confirmation already completed during the current sync run.
                if artifact["state"] == "confirmed" and artifact.get("confirmed_token") == token:
                    if len(nodes) != 1 or nodes[0]["id"] != artifact["receipt"]["id"]:
                        artifact["state"] = "conflict"
                        actions[kind] = "stop_multiple_existing" if len(nodes) > 1 else "stop_registered_node_missing_or_changed"
                    elif (registered and registered.get("version") is not None
                          and registered["version"] != nodes[0].get("version")):
                        artifact["state"] = "conflict"
                        actions[kind] = "stop_external_change"
                    else:
                        actions[kind] = "none"
                else:
                    artifact["state"] = "awaiting_confirmation"
                    actions[kind] = "confirm_recorded_node"
                continue
            state, action = "conflict", "stop_unknown_existing"
            if len(nodes) > 1:
                action = "stop_multiple_existing"
            elif registered:
                if len(nodes) != 1 or nodes[0]["id"] != registered["id"]:
                    action = "stop_registered_node_missing_or_changed"
                elif not registered.get("confirmed"):
                    action = "stop_unconfirmed_previous_write"
                elif registered.get("content_version") == job["version"]:
                    artifact["receipt"] = registered["receipt"]
                    state, action = "awaiting_confirmation", "confirm_recorded_node"
                elif registered.get("version") is None or revision(nodes[0].get("version")) is None:
                    action = "stop_missing_revision"
                elif nodes[0]["version"] != registered["version"]:
                    action = "stop_external_change"
                else:
                    state, action = "update_ready", "update"
                    artifact.update(remote_id=registered["id"], expected_version=registered["version"])
            elif not nodes:
                state, action = "create_ready", "create"
            artifact.update(state=state, observed_token=token, last_evidence=evidence["evidence_ref"])
            actions[kind] = action
        persist(folder, job, token)
        return dict(job_id=job_id, status=job["status"], actions=actions)


def begin_transfer(workspace, token, job_id, kind):
    if kind not in KINDS:
        raise ValueError("unknown artifact kind")
    if not isinstance(job_id, str) or not common.HEX.fullmatch(job_id):
        raise ValueError("invalid delivery job ID")
    workspace, root = common.root_for(workspace)
    # Read only the date first, then use the formal lock order: day -> sync.
    # The authoritative manifest/scope check still occurs inside the sync lock.
    peek = common.read_json(workspace, root / "deliveries" / job_id / "job.json")
    day = support.valid_date(peek["record_date"])
    with support.day_lock(Path(workspace), day) as (paths, targets):
        record = support.read_pair(targets, support.load_journal(Path(workspace), day), day)
        if record is None or record.get("record_status") != "final":
            raise ValueError("current record pair must still be final before transfer")
        current_version = common.sha(common.encoded(dict(learning=common.sha(targets[2].read_bytes()),
                                                          structured=common.sha(targets[1].read_bytes()))))
        with common.locked(workspace) as (workspace, root):
            context = load_context(workspace, root, token)
            if context["status"] != "ready":
                raise ValueError("current target is not writable with the required light capabilities")
            folder, job, _, _, _ = load_job(workspace, root, job_id, context)
            if job["record_date"] != day or job["version"] != current_version:
                raise ValueError("local final record changed; prepare its current version before transferring")
            artifact = job["artifacts"][kind]
            if artifact["state"] not in ("create_ready", "update_ready") or artifact.get("observed_token") != token:
                raise ValueError("fresh complete directory evidence must authorize this write")
            operation = "create" if artifact["state"] == "create_ready" else "update"
            if operation == "update" and "update_file" not in context["capabilities"]:
                raise ValueError("update_file capability is required to replace a registered mirror")
            action_id = uuid.uuid4().hex
            artifact.update(state="uploading", action_id=action_id, operation=operation)
            persist(folder, job, token)
            plan = dict(schema_version=1, job_id=job_id, kind=kind, action_id=action_id, operation=operation,
                        space_id=job["space_id"], filename=artifact["filename"], node_kind=artifact["node_kind"],
                        file=str(folder / artifact["filename"]), next="record_write_receipt_then_confirm_node_metadata")
            if operation == "update":
                plan.update(id=artifact["remote_id"], expected_version=artifact["expected_version"])
                if kind == "learning":
                    plan.update(node_block_id=artifact["remote_id"], confirm_overwrite=True)
                else:
                    plan["node_id"] = artifact["remote_id"]
            return plan


def uploaded(workspace, token, job_id, kind, receipt):
    evidence_object(receipt)
    if kind not in KINDS:
        raise ValueError("unknown artifact kind")
    with common.locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        folder, job, catalog_path, index, entry = load_job(workspace, root, job_id, context)
        artifact = job["artifacts"][kind]
        if artifact["state"] != "uploading":
            raise ValueError("write receipt must follow a permitted transfer")
        if (receipt.get("action_id") != artifact["action_id"] or receipt.get("operation") != artifact["operation"]
                or receipt.get("space_id") != job["space_id"] or receipt.get("filename") != artifact["filename"]
                or receipt.get("node_kind") != artifact["node_kind"]):
            raise ValueError("receipt does not match the exact authorized write")
        node_id = common.required(receipt.get("id"), "receipt.id")
        if artifact["operation"] == "update" and node_id != artifact["remote_id"]:
            raise ValueError("update receipt belongs to another node")
        success = receipt.get("success") is True
        if kind == "learning":
            success = success and all(type(receipt.get(k)) is int and receipt[k] == 0
                                      for k in ("failedCount", "fatalCount"))
        if not success:
            artifact.update(state="uncertain", remote_id=node_id, last_error="write did not report complete success")
            persist(folder, job, token)
            return dict(job_id=job_id, status=job["status"], action="stop_uncertain_write", remote_id=node_id)
        revision(receipt.get("version"))
        artifact.update(state="awaiting_confirmation", remote_id=node_id, receipt=dict(receipt))
        entry["receipts"][kind] = dict(id=node_id, version=None, confirmed=False,
                                       content_version=job["version"], receipt=dict(receipt))
        put_catalog(catalog_path, index)
        persist(folder, job, token)
        return dict(job_id=job_id, status=job["status"], action="confirm_recorded_node",
                    remote_id=node_id, confirmation_level=CONFIRMATION)


def confirm(workspace, token, job_id, kind, metadata):
    evidence_object(metadata)
    if kind not in KINDS:
        raise ValueError("unknown artifact kind")
    with common.locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        folder, job, catalog_path, index, entry = load_job(workspace, root, job_id, context)
        artifact = job["artifacts"][kind]
        receipt = artifact.get("receipt")
        if not receipt or receipt.get("success") is not True:
            raise ValueError("node presence alone cannot substitute for a successful registered write receipt")
        if (metadata.get("exists") is not True or metadata.get("space_id") != job["space_id"]
                or metadata.get("id") != receipt["id"] or metadata.get("node_kind") != artifact["node_kind"]
                or not matches_name(metadata, artifact)):
            raise ValueError("node metadata does not confirm this receipt's target, identity and name/type")
        version = revision(metadata.get("version"))
        registered = entry["receipts"].get(kind, {})
        if (registered.get("confirmed") is True and registered.get("content_version") == job["version"]
                and registered.get("id") == receipt["id"] and registered.get("version") is not None
                and registered["version"] != version):
            artifact.update(state="conflict", last_error="remote revision changed since the registered write")
            persist(folder, job, token)
            raise ValueError("remote revision changed; stop_external_change and retain the original baseline")
        if (artifact.get("operation") == "update"
                and (version is None or version == artifact.get("expected_version"))):
            artifact.update(state="awaiting_confirmation", last_evidence=metadata["evidence_ref"])
            persist(folder, job, token)
            return dict(job_id=job_id, kind=kind, status=job["status"], artifact_state="awaiting_confirmation",
                        action="retain_pending_update", confirmation_level=CONFIRMATION)
        if receipt.get("version") is not None and receipt["version"] != version:
            raise ValueError("node revision differs from the successful write receipt")
        artifact.update(state="confirmed", confirmed_token=token, remote_id=receipt["id"],
                        confirmation_level=CONFIRMATION, last_evidence=metadata["evidence_ref"])
        entry["receipts"][kind] = dict(id=receipt["id"], version=version, confirmed=True,
                                       content_version=job["version"], receipt=receipt)
        put_catalog(catalog_path, index)
        persist(folder, job, token)
        return dict(job_id=job_id, kind=kind, status=job["status"], artifact_state="confirmed",
                    confirmation_level=CONFIRMATION)


def fail(workspace, token, job_id, kind, message):
    common.required(message, "message")
    if kind not in KINDS:
        raise ValueError("unknown artifact kind")
    with common.locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        folder, job, _, _, _ = load_job(workspace, root, job_id, context)
        artifact = job["artifacts"][kind]
        # A failed call can have taken effect remotely. No blind same-round retry.
        previous_state = artifact["state"]
        state = ("uncertain" if previous_state in ("uploading", "uncertain")
                 else previous_state if previous_state in ("awaiting_confirmation", "conflict") else "failed")
        artifact.update(state=state, last_error=message[:1000])
        persist(folder, job, token)
        return dict(job_id=job_id, status=job["status"], local_records_untouched=True,
                    action="confirm_recorded_node" if artifact.get("receipt") else "stop_and_retain_delivery")


def pending(workspace, token, day=None):
    """List only registered latest jobs in the current account/space, no bodies.

    An explicit day narrows the result. Omitting it can resume a previously
    registered pending day; it never scans local record folders or legacy jobs.
    """
    if day is not None:
        day = support.valid_date(day)
    with common.locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        if not context.get("selected") or context["status"] != "ready":
            return {"jobs": []}
        _, index = catalog(workspace, root)
        jobs = []
        scopes = index["scopes"]
        if day is not None:
            key = scope_for(context, day)
            scopes = {key: scopes[key]} if key in scopes else {}
        for scope, entry in sorted(scopes.items()):
            job_id = entry.get("latest_job_id") if isinstance(entry, dict) else None
            if not isinstance(job_id, str) or not common.HEX.fullmatch(job_id):
                continue
            path = common.safe_path(workspace, root / "deliveries" / job_id / "job.json")
            peek = common.read_json(workspace, path)
            if not isinstance(peek, dict):
                continue
            same_account = (peek.get("account_id") == context["account_id"]
                            and (context["account_id"] is not None or peek.get("session_id") == context["session_id"]))
            if not same_account or peek.get("space_id") != context["selected"]["id"]:
                continue
            if peek.get("scope") != scope:
                raise ValueError("registered delivery scope changed; retain it for diagnosis")
            _, job, _, _, _ = load_job(workspace, root, job_id, context, verify_payload=False)
            if job["status"] not in ("synced", "superseded"):
                jobs.append(dict(job_id=job["job_id"], record_date=job["record_date"], status=job["status"]))
        return {"jobs": jobs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    discover_parser = commands.add_parser("discover")
    discover_parser.add_argument("--host-json", type=Path, required=True)
    choice = commands.add_parser("select")
    choice.add_argument("--token", required=True)
    choice.add_argument("--space-id", required=True)
    for command in ("prepare", "inspect", "begin-transfer", "uploaded", "confirm", "fail", "pending"):
        child = commands.add_parser(command)
        child.add_argument("--token", required=True)
        if command in ("prepare", "pending"):
            child.add_argument("--day", required=command == "prepare")
        else:
            child.add_argument("--job-id", required=True)
        if command in ("begin-transfer", "uploaded", "confirm", "fail"):
            child.add_argument("--kind", choices=KINDS, required=True)
        if command in ("inspect", "uploaded", "confirm"):
            child.add_argument("--evidence-json", type=Path, required=True)
        if command == "fail":
            child.add_argument("--message", required=True)
    args = parser.parse_args()
    try:
        if args.command == "discover":
            result = discover(args.workspace, common.read_json(args.workspace, args.host_json))
        elif args.command == "select":
            result = select(args.workspace, args.token, args.space_id)
        elif args.command == "prepare":
            result = prepare(args.workspace, args.token, args.day)
        elif args.command == "pending":
            result = pending(args.workspace, args.token, args.day)
        elif args.command == "inspect":
            result = inspect(args.workspace, args.token, args.job_id, common.read_json(args.workspace, args.evidence_json))
        elif args.command == "begin-transfer":
            result = begin_transfer(args.workspace, args.token, args.job_id, args.kind)
        elif args.command == "fail":
            result = fail(args.workspace, args.token, args.job_id, args.kind, args.message)
        else:
            method = uploaded if args.command == "uploaded" else confirm
            result = method(args.workspace, args.token, args.job_id, args.kind,
                            common.read_json(args.workspace, args.evidence_json))
        print(json.dumps(dict(ok=True, **result), ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, WorkspaceRuntimeError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
