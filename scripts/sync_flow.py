#!/usr/bin/env python3
"""Compose registered WorkBuddy deliveries and capture native receipts in code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import team_space_delivery as delivery
import team_space_sync as common
from library_transfer import LibraryWriter, run_native
from platform_lock import nonblocking_file_lock
from workbuddy_library import LibraryClient, LibraryError, text_id


def _identity(context, session_id, account_id):
    if context["session_id"] != session_id or context["account_id"] != account_id:
        raise ValueError("delivery context belongs to another current session/account; discover again")


def _context(workspace, reader, session_id, account_id, token=None, space_id=None):
    if token:
        with common.locked(workspace) as (workspace, root):
            context = delivery.load_context(workspace, root, token)
            _identity(context, session_id, account_id)
    else:
        if space_id:
            raise ValueError("space selection requires its current discovery token")
        context = delivery.discover(workspace, reader.discover_host(session_id, account_id=account_id, light=True))
    if space_id:
        if context["status"] == "needs_selection":
            if space_id not in {item["id"] for item in context["matches"]}:
                raise ValueError("selection is not in the actual discovery result")
            # A learner choice can arrive later: recheck its actual visibility
            # and permissions rather than silently choosing a different target.
            context = delivery.discover(workspace, reader.discover_host(session_id, account_id=account_id, light=True))
            if space_id not in {item["id"] for item in context["matches"]}:
                raise ValueError("selected learning space is no longer available; discover again")
            if context["selected"] is None:
                context = delivery.select(workspace, context["token"], space_id)
            elif context["selected"]["id"] != space_id:
                raise ValueError("space selection changed; retain local records and choose again")
        elif not context.get("selected") or context["selected"]["id"] != space_id:
            raise ValueError("selection does not match the current selected learning space")
    return context


def _context_result(context):
    return dict(status=context["status"], token=context["token"], target=context.get("selected"),
                matches=context["matches"], local_records_untouched=True)


def _job(workspace, token, job_id):
    with common.locked(workspace) as (workspace, root):
        context = delivery.load_context(workspace, root, token)
        folder, job, _, _, _ = delivery.load_job(workspace, root, job_id, context)
        return folder, job


def _result(workspace, context, job_id, **extra):
    _, job = _job(workspace, context["token"], job_id)
    return dict(status=job["status"], token=context["token"], job_id=job_id,
                record_date=job["record_date"], target=context["selected"],
                artifacts={kind: {"state": item["state"], "id": item.get("remote_id")}
                           for kind, item in job["artifacts"].items()},
                local_records_untouched=True, **extra)


def _candidate(folder, job, kind):
    item = job["artifacts"][kind]
    plan = dict(schema_version=1, job_id=job["job_id"], kind=kind,
                operation="update" if item["state"] == "update_ready" else "create",
                space_id=job["space_id"], filename=item["filename"], node_kind=item["node_kind"],
                file=str(folder / item["filename"]))
    if plan["operation"] == "update":
        plan.update(id=item["remote_id"], expected_version=item["expected_version"])
        if kind == "learning":
            plan.update(node_block_id=item["remote_id"], confirm_overwrite=True)
        else:
            plan["node_id"] = item["remote_id"]
    return plan


def _receipt_path(workspace, folder, action_id):
    if not isinstance(action_id, str) or not re.fullmatch(r"[0-9a-f]{32}", action_id):
        raise ValueError("invalid registered write action identifier")
    return common.safe_path(workspace, folder / ("native-receipt-" + action_id + ".json"))


def _restore_receipts(workspace, token, job_id):
    """A crash after a receipt was saved must not cause a second native write."""
    folder, job = _job(workspace, token, job_id)
    for kind in delivery.KINDS:
        item = job["artifacts"][kind]
        if item["state"] == "uploading" and item.get("action_id"):
            path = _receipt_path(workspace, folder, item["action_id"])
            if path.exists():
                delivery.uploaded(workspace, token, job_id, kind, common.read_json(workspace, path))


def _check_update(reader, plan):
    metadata = reader.node_info(plan["id"])
    if (metadata.get("exists") is not True or metadata.get("id") != plan["id"]
            or metadata.get("space_id") != plan["space_id"]
            or metadata.get("node_kind") != plan["node_kind"]
            or metadata.get("filename") not in (plan["filename"], Path(plan["filename"]).stem)
            or metadata.get("version") != plan["expected_version"]):
        raise LibraryError("registered mirror changed before replacement; retain it for diagnosis")


def _advance(workspace, reader, context, *, day=None, job_id=None, execute=False, writer=None):
    token = context["token"]
    if job_id is None:
        if day is None:
            raise ValueError("run needs a record day or a registered job identifier")
        prepared = delivery.prepare(workspace, token, day)
        job_id = prepared["job_id"]
    folder, job = _job(workspace, token, job_id)
    if day is not None and day != job["record_date"]:
        raise ValueError("registered delivery date differs from the requested day")
    _restore_receipts(workspace, token, job_id)
    folder, job = _job(workspace, token, job_id)
    if job["status"] == "synced" and all(item.get("confirmed_token") == token for item in job["artifacts"].values()):
        return _result(workspace, context, job_id)
    if any(item["state"] in ("uploading", "uncertain") for item in job["artifacts"].values()):
        return _result(workspace, context, job_id, next_action="stop_uncertain_write")

    if any(item["state"] in ("unchecked", "failed")
           or (item.get("observed_token") != token and item.get("confirmed_token") != token)
           for item in job["artifacts"].values()):
        rows = reader.list_nodes(context["selected"]["id"])
        evidence = dict(schema_version=1, space_id=context["selected"]["id"], lookup_complete=True,
                        evidence_ref="official-library:space.workspace.list-nodes:all-pages",
                        nodes=[reader.delivery_node(row, context["selected"]["id"]) for row in rows])
        delivery.inspect(workspace, token, job_id, evidence)
    folder, job = _job(workspace, token, job_id)
    if job["status"] == "conflict":
        return _result(workspace, context, job_id, next_action="stop_conflict")

    # Existing successful receipts need only their node confirmation, even if
    # this host cannot currently execute the write scripts.
    for kind in delivery.KINDS:
        item = job["artifacts"][kind]
        if item.get("receipt") and not (item["state"] == "confirmed" and item.get("confirmed_token") == token):
            try:
                delivery.confirm(workspace, token, job_id, kind, reader.node_info(item["receipt"]["id"]))
            except (OSError, ValueError) as error:
                return _result(workspace, context, job_id, next_action="confirm_recorded_node", error=str(error))
    folder, job = _job(workspace, token, job_id)
    if job["status"] == "synced":
        return _result(workspace, context, job_id)
    if any(item["state"] in ("awaiting_confirmation", "uploading", "uncertain", "conflict")
           for item in job["artifacts"].values()):
        return _result(workspace, context, job_id, next_action="retain_pending_delivery")

    plans = {kind: _candidate(folder, job, kind) for kind in delivery.KINDS
             if job["artifacts"][kind]["state"] in ("create_ready", "update_ready")}
    if not plans:
        return _result(workspace, context, job_id, next_action="retain_pending_delivery")
    writer = writer or LibraryWriter(reader.root, python=reader.python)
    # Check every needed native contract before registering either actual write.
    for plan in plans.values():
        writer.preflight(plan)
    if not execute:
        result = _result(workspace, context, job_id, next_action="load_required_rules_then_execute")
        result.update(status="ready_to_write", required_rules=["doc" if kind == "learning" else "drive" for kind in plans])
        return result
    for kind, candidate in plans.items():
        if candidate["operation"] == "update":
            _check_update(reader, candidate)
        plan = delivery.begin_transfer(workspace, token, job_id, kind)
        try:
            receipt = writer.write(plan)
        except (OSError, ValueError) as error:
            delivery.fail(workspace, token, job_id, kind, "native write returned no reliable receipt")
            return _result(workspace, context, job_id, next_action="stop_uncertain_write", error=str(error))
        # Save the actual normalized receipt before any other local/cloud work.
        # It can be re-registered after an interruption, never regenerated by a model.
        path = _receipt_path(workspace, folder, plan["action_id"])
        common.atomic(path, common.encoded(receipt))
        delivery.uploaded(workspace, token, job_id, kind, receipt)
        if receipt["success"] is not True:
            return _result(workspace, context, job_id, next_action="stop_uncertain_write")
        try:
            delivery.confirm(workspace, token, job_id, kind, reader.node_info(receipt["id"]))
        except (OSError, ValueError) as error:
            return _result(workspace, context, job_id, next_action="confirm_recorded_node", error=str(error))
        _, current = _job(workspace, token, job_id)
        if current["artifacts"][kind]["state"] != "confirmed":
            return _result(workspace, context, job_id, next_action="confirm_recorded_node")
    return _result(workspace, context, job_id)


def run_flow(workspace, library_dir, session_id, *, account_id=None, day=None, job_id=None,
             token=None, space_id=None, execute=False, client=None, writer=None, command="run"):
    session_id = text_id(session_id, "session_id")
    if account_id is not None:
        text_id(account_id, "account_id")
    if day is not None:
        day = delivery.support.valid_date(day)
    if command == "run" and day is None and job_id is None:
        raise ValueError("run needs --day or --job-id")
    if command == "pending" and not token:
        raise ValueError("pending needs the current discovery token")
    reader = client or LibraryClient(library_dir, runner=run_native)
    workspace, root = common.root_for(workspace)
    with nonblocking_file_lock(common.safe_path(workspace, root / "flow.lock"),
                               busy_message="another delivery flow is running; retain local records"):
        context = _context(workspace, reader, session_id, account_id, token, space_id)
        if context["status"] != "ready" or command == "discover":
            return _context_result(context)
        if command == "pending":
            return dict(**_context_result(context), **delivery.pending(workspace, context["token"], day))
        return _advance(workspace, reader, context, day=day, job_id=job_id, execute=execute, writer=writer)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--library-dir", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--account-id")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover")
    pending = commands.add_parser("pending")
    pending.add_argument("--token", required=True)
    pending.add_argument("--day")
    run = commands.add_parser("run")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--day")
    source.add_argument("--job-id")
    run.add_argument("--token")
    run.add_argument("--space-id")
    run.add_argument("--execute", action="store_true", help="host has loaded the required official write rules")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        result = run_flow(args.workspace, args.library_dir, args.session_id, account_id=args.account_id,
                          day=getattr(args, "day", None), job_id=getattr(args, "job_id", None),
                          token=getattr(args, "token", None), space_id=getattr(args, "space_id", None),
                          execute=getattr(args, "execute", False), command=args.command)
    except (OSError, ValueError, KeyError, TypeError) as error:
        message = "local delivery operation failed; preserve records and registered receipts" if isinstance(error, OSError) else str(error)
        result = dict(status="blocked", error=message, local_records_untouched=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"synced", "ready", "ready_to_write", "needs_selection", "skipped_no_space", "local_only"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
