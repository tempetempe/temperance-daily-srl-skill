#!/usr/bin/env python3
"""RC1-RC3 compatibility and explicit content diagnostics.

Normal RC4 delivery uses team_space_delivery.py and metadata confirmation.

JSON inputs below are this helper's adapter contract, NOT WorkBuddy API schemas.
The helper validates scope/content and keeps an outbox; it never makes network calls.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import support_events as support
from workspace_runtime import ensure_workspace, ensure_directory, WorkspaceRuntimeError
from platform_lock import nonblocking_file_lock

HEX = re.compile(r"[0-9a-f]{64}\Z")
SPACE_NAME = re.compile(r"(?P<name>[^\d\r\n]+?)(?P<number>[0-9]+)的学习空间\Z")
KINDS = ("learning", "structured")
REQUIRED = {"lookup_files", "create_file"}
TTL_SECONDS = 900
MAX_ATTEMPTS = 2


def stamp():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def required(value, label):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be a nonempty string without control characters")
    return value


def match_space_name(name):
    if not isinstance(name, str):
        return False
    match = SPACE_NAME.fullmatch(name.strip())
    if not match:
        return False
    learner = match['name']
    def letter(char):
        label = unicodedata.name(char, "")
        return char.isalpha() and ("LATIN" in label or label.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")))
    return (letter(learner[0]) and letter(learner[-1])
            and all(letter(c) or c in " .·'-" for c in learner))


def safe_path(workspace, path):
    """Only within the explicitly authorized Workspace, without symlink traversal."""
    workspace = Path(workspace).resolve()
    path = Path(path)
    if not path.is_absolute():
        path = workspace / path
    if ".." in path.parts:
        raise ValueError("parent path traversal is not allowed")
    # macOS exposes system aliases such as /var -> /private/var. Accept an
    # alias of the authorized root, but still inspect every descendant for links.
    for prefix in reversed((path, *path.parents)):
        if prefix.resolve() == workspace:
            relative = path.relative_to(prefix)
            break
    else:
        raise ValueError("path is outside the authorized Workspace")
    cursor = workspace
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink is not allowed: {cursor}")
    return workspace / relative


def root_for(workspace):
    paths = ensure_workspace(Path(workspace))
    root = safe_path(paths.workspace, paths.root / "sync")
    ensure_directory(root)
    return paths.workspace, root


def atomic(path, data):
    ensure_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".sync-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(workspace):
    workspace, root = root_for(workspace)
    with nonblocking_file_lock(safe_path(workspace, root / "sync.lock"),
                               busy_message="another sync helper is running; retry after it finishes"):
        yield workspace, root


def read_json(workspace, path):
    return json.loads(safe_path(workspace, path).read_text(encoding="utf-8"))


def load_context(workspace, root, token):
    context = read_json(workspace, root / "context.json")
    if not isinstance(context, dict) or context.get("schema_version") != 1 or context.get("token") != token:
        raise ValueError("context changed; run discovery again")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(context["checked_at"])).total_seconds()
    if age < 0 or age > TTL_SECONDS:
        raise ValueError("space discovery expired; query the current account again")
    return context


def ready_status(context):
    target = context.get("selected")
    if not target:
        return context["status"]
    if target["can_write"] is False:
        return "permission_denied"
    if target["can_write"] is not True:
        return "permission_unknown"
    capabilities = set(context["capabilities"])
    if context.get("profile") == "workbuddy-library-light-v1":
        return "capability_missing" if (REQUIRED | {"inspect_file"}) - capabilities else "ready"
    if REQUIRED - capabilities or not capabilities.intersection({"read_file", "download_file"}):
        return "capability_missing"
    if context.get("profile") == "workbuddy-library-v0.6" and "read_document" not in capabilities:
        return "capability_missing"
    return "ready"


def discover(workspace, host):
    if not isinstance(host, dict) or host.get("schema_version") != 1:
        raise ValueError("host context requires schema_version 1")
    environment = host.get("host", "workbuddy")
    if environment not in ("workbuddy", "other", "unknown"):
        raise ValueError("host must be workbuddy, other or unknown")
    required(host.get("host_evidence"), "host_evidence")
    session = required(host.get("session_id"), "session_id")
    account = host.get("account_id")
    if account is not None:
        required(account, "account_id")
    capabilities = host.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities):
        raise ValueError("capabilities must be an array of names")
    discovery = host.get("capability_discovery", {})
    discovered = (isinstance(discovery, dict) and discovery.get("complete") is True
                  and discovery.get("library_skill") in ("loaded", "unavailable")
                  and isinstance(discovery.get("evidence_ref"), str) and bool(discovery["evidence_ref"].strip()))
    result = dict(schema_version=1, token=uuid.uuid4().hex, session_id=session, account_id=account,
                  checked_at=stamp(), capabilities=capabilities, selected=None, matches=[],
                  profile=host.get("profile"), capability_discovery=discovery)
    if environment == "other":
        result["status"] = "local_only"
    elif environment == "workbuddy" and not discovered:
        result.update(status="needs_capability_discovery", next="load_builtin_library_skill")
    elif (environment == "unknown" or host.get("query_ok") is not True
          or host.get("complete") is not True or "list_spaces" not in capabilities):
        result["status"] = "discovery_failed"
        result["reason"] = "host or complete space enumeration unavailable"
    else:
        required(host.get("list_evidence"), "list_evidence")
        spaces = host.get("spaces")
        if not isinstance(spaces, list):
            raise ValueError("spaces must be an array")
        ids = set()
        for space in spaces:
            if not isinstance(space, dict):
                raise ValueError("each space must be an object")
            space_id = required(space.get("id"), "space.id")
            name = space.get("name")
            if not isinstance(name, str):
                raise ValueError("space.name must be a string")
            if space_id in ids:
                raise ValueError("duplicate space ID; reconcile pagination before discovery")
            ids.add(space_id)
            writable = space.get("can_write")
            if writable is not None and type(writable) is not bool:
                raise ValueError("space.can_write must be true, false or null")
            if space.get("category") == "team" and match_space_name(name):
                result["matches"].append(dict(id=space_id, name=name, can_write=writable))
        result["status"] = "needs_selection" if result["matches"] else "skipped_no_space"
        if len(result["matches"]) == 1:
            result["selected"] = result["matches"][0]
    with locked(workspace) as (workspace, root):
        previous_path = safe_path(workspace, root / "context.json")
        # Only reuse a choice with a reliably identical account and host conversation.
        if len(result["matches"]) > 1 and account and previous_path.exists():
            previous = read_json(workspace, previous_path)
            if (previous.get("session_id") == session and previous.get("account_id") == account
                    and previous.get("selected")):
                result["selected"] = next((x for x in result["matches"]
                                            if x["id"] == previous["selected"]["id"]), None)
        result["status"] = ready_status(result)
        atomic(previous_path, encoded(result))
    return result


def select(workspace, token, space_id):
    with locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        if context["status"] != "needs_selection":
            raise ValueError("no unresolved multiple-space choice")
        selected = next((x for x in context["matches"] if x["id"] == space_id), None)
        if selected is None:
            raise ValueError("chosen space is not in the complete current result")
        context["selected"] = selected
        context["status"] = ready_status(context)
        atomic(root / "context.json", encoded(context))
        return context


def wrap_json(data):
    """Choose a fence longer than anything in the JSON; preserve every source byte."""
    content = data.decode("utf-8")
    json.loads(content)
    width = max([2, *(len(x) for x in re.findall(r"`+", content))]) + 1
    fence = "`" * width
    return ("# Daily SRL 原始结构化记录\n\n" + fence + "json\n").encode() + data + b"\n" + fence.encode() + b"\n"


def unwrap_json(data):
    prefix = "# Daily SRL 原始结构化记录\n\n".encode("utf-8")
    if not data.startswith(prefix):
        raise ValueError("not a Daily SRL JSON envelope")
    first, body = data[len(prefix):].split(b"\n", 1)
    if not re.fullmatch(rb"`{3,}json", first):
        raise ValueError("invalid JSON envelope fence")
    suffix = b"\n" + first[:-4] + b"\n"
    if not body.endswith(suffix):
        raise ValueError("invalid JSON envelope ending")
    raw = body[:-len(suffix)]
    json.loads(raw.decode("utf-8"))
    return raw


def payload_specs(day, learning, structured, mode):
    version = sha(encoded(dict(learning=sha(learning), structured=sha(structured))))
    values = {"learning": (learning, "md"), "structured":
              (structured, "json") if mode == "json" else (wrap_json(structured), "md")}
    artifacts = {}
    for kind, (data, extension) in values.items():
        filename = f"Daily-SRL-{day}-{version}-{kind}.{extension}"
        artifacts[kind] = dict(filename=filename, sha256=sha(data), size=len(data),
                               remote_key=f"daily-srl/v1/{day}/{version}/{kind}/{extension}", state="unchecked")
    return version, values, artifacts


def job_identity(job):
    return sha(encoded(dict(space_id=job["target"]["id"], account_id=job["account_id"],
                            unknown_account_session=job["session_id"] if job["account_id"] is None else None,
                            record_date=job["record_date"], version=job["version"], mode=job["mode"])))


def load_job(workspace, root, job_id, context):
    if not HEX.fullmatch(job_id):
        raise ValueError("invalid job ID")
    folder = safe_path(workspace, root / "outbox" / job_id)
    job = read_json(workspace, folder / "job.json")
    if (not isinstance(job, dict) or job.get("schema_version") != 1 or job.get("job_id") != job_id
            or job_identity(job) != job_id or job.get("mode") not in ("json", "json-md")):
        raise ValueError("invalid outbox manifest; preserve for diagnosis")
    support.valid_date(job["record_date"])
    if not isinstance(job["version"], str) or not HEX.fullmatch(job["version"]):
        raise ValueError("invalid content version")
    for kind in KINDS:
        extension = "md" if kind == "learning" or job["mode"] == "json-md" else "json"
        expected_name = f"Daily-SRL-{job['record_date']}-{job['version']}-{kind}.{extension}"
        if job["artifacts"][kind]["filename"] != expected_name:
            raise ValueError("outbox filename changed")
    if not context.get("selected") or context["selected"]["id"] != job["target"]["id"]:
        raise ValueError("job belongs to a different target; never retarget pending data")
    if job["account_id"] != context["account_id"] or (job["account_id"] is None
                                                            and job["session_id"] != context["session_id"]):
        raise ValueError("cannot establish the original account; do not resume this job")
    learning = safe_path(workspace, folder / job["artifacts"]["learning"]["filename"]).read_bytes()
    structured = safe_path(workspace, folder / "source-structured.json").read_bytes()
    version, values, expected = payload_specs(job["record_date"], learning, structured, job["mode"])
    if version != job["version"]:
        raise ValueError("snapshot source changed; preserve outbox")
    for kind in KINDS:
        actual = job["artifacts"][kind]
        for key in ("filename", "sha256", "size", "remote_key"):
            if actual[key] != expected[kind][key]:
                raise ValueError("outbox artifact metadata changed")
        payload = safe_path(workspace, folder / actual["filename"])
        if payload.read_bytes() != values[kind][0]:
            raise ValueError("outbox payload changed; do not upload")
    return folder, job


def persist_job(folder, job, token):
    states = [a["state"] for a in job["artifacts"].values()]
    if all(a.get("verified_token") == token and a["state"] == "verified" for a in job["artifacts"].values()):
        job["status"] = "synced"
    elif "conflict" in states:
        job["status"] = "conflict"
    elif "awaiting_acceptance" in states:
        job["status"] = "awaiting_acceptance"
    elif any(x in states for x in ("uploaded_unverified", "existing_unverified")):
        job["status"] = "awaiting_verification"
    else:
        job["status"] = "pending"
    job["updated_at"] = stamp()
    atomic(folder / "job.json", encoded(job))


def prepare(workspace, token, day, mode="json"):
    support.valid_date(day)
    if mode not in ("json", "json-md"):
        raise ValueError("unsupported structured format")
    # Reuse the formal day lock and integrity checks; never take a guessed HTML snapshot.
    with support.day_lock(Path(workspace), day) as (paths, targets):
        record = support.read_pair(targets, support.load_journal(Path(workspace), day), day)
        if record is None or record.get("record_status") != "final":
            raise ValueError("only a verified final record pair can be synchronized")
        learning, structured = targets[2].read_bytes(), targets[1].read_bytes()
    with locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        if not context.get("selected"):
            raise ValueError("no target selected; local-only/zero-match/choice is not an upload job")
        version, values, artifacts = payload_specs(day, learning, structured, mode)
        job = dict(schema_version=1, account_id=context["account_id"], session_id=context["session_id"],
                   target=context["selected"], record_date=day, version=version, mode=mode,
                   artifacts=artifacts, created_at=stamp(), status="pending")
        job_id = job["job_id"] = job_identity(job)
        folder = safe_path(workspace, root / "outbox" / job_id)
        ensure_directory(safe_path(workspace, root / "incoming"))
        if (folder / "job.json").exists():
            _, job = load_job(workspace, root, job_id, context)
        else:
            ensure_directory(folder)
            for name, data in [(artifacts[k]["filename"], values[k][0]) for k in KINDS] + [("source-structured.json", structured)]:
                path = safe_path(workspace, folder / name)
                if path.exists() and path.read_bytes() != data:
                    raise ValueError("partial snapshot conflicts; preserve for diagnosis")
                if not path.exists():
                    atomic(path, data)
        persist_job(folder, job, token)
        return dict(job_id=job_id, status=job["status"], target=context["selected"],
                    readiness=context["status"], artifacts=job["artifacts"],
                    profile=context.get("profile"),
                    legacy_lookup_names={kind: legacy_names(day, kind) for kind in KINDS})


def legacy_names(day, kind):
    """Only the two names seen in the RC2 field test, scoped to this exact day."""
    return ([f"Daily-SRL-{day}-学习记录", f"Daily-SRL-{day}-学习记录.md"] if kind == "learning"
            else [f"Daily-SRL-{day}-structured.json"])


def readback_matches(workspace, root, folder, artifact, item, native, source_override=None):
    if item.get("applied") is not True:
        return "awaiting_acceptance", None
    path_value = item.get("content_file")
    if not path_value:
        return "existing_unverified", None
    remote_path = safe_path(workspace, required(path_value, "content_file"))
    remote_path.relative_to(root / "incoming")
    data = remote_path.read_bytes()
    object_type = item.get("object_type", "original_file" if not native else None)
    if object_type == "imported_document":
        from document_readback import verify_document
        if item.get("node_kind") != "doc" or item.get("readback_method") != "document":
            raise ValueError("native document requires full official document readback")
        if data.startswith(b"KS_DOC_REVIEWS"):
            fields = data.partition(b"\n")[0].rstrip(b"\r").split(b"\t")
            if len(fields) != 4 or fields[1] != required(item.get("id"), "remote.id").encode("utf-8"):
                raise ValueError("full-document readback belongs to a different remote node")
        source = source_override if source_override is not None else safe_path(workspace, folder / artifact["filename"]).read_bytes()
        # A document's representation can change. This verifies full visible text,
        # not the original Markdown bytes. The JSON original has its own byte check.
        try:
            matches = verify_document(source, data)
        except ValueError:
            matches = False
        return ("verified", "document_full_text") if matches else ("uploaded_unverified", None)
    if object_type != "original_file":
        raise ValueError("remote representation must be declared")
    if native and (item.get("node_kind") not in ("drive", "smh")
                   or item.get("readback_method") != "download"):
        raise ValueError("original files require the official download, not parsed/search content")
    return (("verified", "original_bytes") if sha(data) == artifact["sha256"] and len(data) == artifact["size"]
            else ("conflict", None))


def distinct_legacy_version(workspace, root, folder, job, kind, item, native):
    """A complete independently downloaded legacy JSON can establish another version.

    A flag/title alone cannot dismiss a mismatching candidate. The corresponding
    doc must also match the old JSON's canonical rendering before it is ignored.
    """
    baseline = item.get("baseline_structured_file")
    if not baseline or item.get("applied") is not True:
        return False
    path = safe_path(workspace, required(baseline, "baseline_structured_file"))
    path.relative_to(root / "incoming")
    raw = path.read_bytes()
    try:
        import review_records as records
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get("record_date") != job["record_date"]
                or record.get("record_status") != "final"):
            return False
        learning = records.render_complete_record(record).encode("utf-8")
        records.verify_render_integrity(record, learning.decode("utf-8"))
        version, values, specs = payload_specs(job["record_date"], learning, raw, job["mode"])
        if version == job["version"]:
            return False
        spec = dict(specs[kind], filename=job["artifacts"][kind]["filename"])
        state, _ = readback_matches(workspace, root, folder, spec, item, native,
                                    source_override=values[kind][0])
        return state == "verified"
    except (ValueError, KeyError, TypeError):
        return False


def observe(workspace, token, job_id, kind, evidence):
    if kind not in KINDS or not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("invalid observation")
    required(evidence.get("evidence_ref"), "evidence_ref")
    if evidence.get("lookup_complete") is not True or not isinstance(evidence.get("matches"), list):
        raise ValueError("complete exact-name lookup is required before upload or deduplication")
    with locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        folder, job = load_job(workspace, root, job_id, context)
        artifact = job["artifacts"][kind]
        if evidence.get("space_id") != job["target"]["id"] or evidence.get("filename") != artifact["filename"]:
            raise ValueError("lookup scope/name does not match the immutable payload")
        native = context.get("profile") == "workbuddy-library-v0.6"
        matches = evidence["matches"]
        state, action = "conflict", "stop_conflict"
        remote_id = None
        integrity_mode = None
        aliases = []
        if not matches and artifact.get("upload_receipt"):
            state, action = "existing_unverified", "lookup_recorded_node"
            remote_id = artifact["upload_receipt"]["id"]
        elif not matches:
            # New native integrations must also check the known RC2 aliases,
            # before a fresh device can mistake an old successful upload for absence.
            if native and (evidence.get("candidate_scan_complete") is not True
                           or not isinstance(evidence.get("candidates"), list)):
                raise ValueError("complete scoped legacy-name lookup is required before creation")
            candidates = evidence.get("candidates", [])
            if candidates:
                states = []
                modes = []
                for item in candidates:
                    if (item.get("space_id") != job["target"]["id"]
                            or item.get("filename") not in legacy_names(job["record_date"], kind)):
                        raise ValueError("legacy candidate is outside the permitted space/date/name")
                    required(item.get("id"), "remote.id")
                    checked, mode = readback_matches(workspace, root, folder, artifact, item, native)
                    if checked != "verified" and distinct_legacy_version(workspace, root, folder, job, kind, item, native):
                        checked, mode = "verified_distinct_version", None
                    states.append(checked); modes.append(mode)
                    aliases.append(dict(remote_id=item["id"], actual_filename=item["filename"],
                                        canonical_filename=artifact["filename"]))
                if all(s in ("verified", "verified_distinct_version") for s in states) and "verified" in states:
                    state, action = "verified", "none"
                    chosen = states.index("verified")
                    remote_id = candidates[chosen]["id"]
                    integrity_mode = modes[chosen]
                    aliases = [alias for alias, checked in zip(aliases, states) if checked == "verified"]
                elif all(s == "verified_distinct_version" for s in states):
                    state, action = "absent", "upload"
                else:
                    state, action = "existing_unverified", "stop_unverified_existing"
            else:
                state, action = "absent", "upload"
        elif len(matches) == 1:
            item = matches[0]
            remote_id = required(item.get("id"), "remote.id")
            names = [artifact["filename"]]
            if item.get("object_type") == "imported_document":
                names.append(Path(artifact["filename"]).stem)
            if item.get("space_id") != job["target"]["id"] or item.get("filename") not in names:
                raise ValueError("remote file has a different parent/name")
            state, integrity_mode = readback_matches(workspace, root, folder, artifact, item, native)
            action = {"verified": "none", "awaiting_acceptance": "wait_for_effective_content",
                      "uploaded_unverified": "wait_for_full_document_verification",
                      "existing_unverified": "stop_unverified_existing"}.get(state, "stop_conflict")
        artifact.update(state=state, remote_id=remote_id, last_evidence=evidence["evidence_ref"],
                        observed_token=token)
        artifact.pop("verified_token", None)
        artifact.pop("integrity_mode", None)
        if state == "verified":
            artifact["verified_token"] = token
            artifact["integrity_mode"] = integrity_mode
            artifact["aliases"] = aliases
        persist_job(folder, job, token)
        return dict(job_id=job_id, kind=kind, artifact_state=state, action=action, status=job["status"])


def uploaded(workspace, token, job_id, kind, receipt):
    """Commit returned identity immediately, before eventual indexing/readback.

    A receipt never verifies content, but prevents another create during delay.
    """
    if kind not in KINDS or not isinstance(receipt, dict):
        raise ValueError("invalid upload receipt")
    required(receipt.get("evidence_ref"), "evidence_ref")
    remote_id = required(receipt.get("id"), "remote.id")
    with locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        folder, job = load_job(workspace, root, job_id, context)
        artifact = job["artifacts"][kind]
        if artifact["state"] != "uploading":
            raise ValueError("receipt must follow a permitted upload")
        names = {artifact["filename"], Path(artifact["filename"]).stem}
        if receipt.get("space_id") != job["target"]["id"] or receipt.get("filename") not in names:
            raise ValueError("upload receipt has the wrong target/name")
        artifact.update(state="existing_unverified", upload_receipt=dict(id=remote_id,
                        evidence_ref=receipt["evidence_ref"], filename=receipt["filename"]))
        persist_job(folder, job, token)
        return dict(job_id=job_id, kind=kind, status=job["status"], remote_id=remote_id,
                    action="read_recorded_node", verified=False)


def begin_upload(workspace, token, job_id, kind):
    if kind not in KINDS:
        raise ValueError("invalid artifact kind")
    with locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        if context["status"] != "ready":
            raise ValueError(f"target cannot be written: {context['status']}")
        folder, job = load_job(workspace, root, job_id, context)
        artifact = job["artifacts"][kind]
        if artifact["state"] != "absent" or artifact.get("observed_token") != token:
            raise ValueError("fresh complete remote lookup must prove absence before upload")
        attempts = artifact.get("attempts", {})
        count = attempts.get(token, 0)
        if count >= MAX_ATTEMPTS:
            raise ValueError("retry limit reached; retain outbox for a later normal run")
        attempts[token] = count + 1
        artifact.update(state="uploading", attempts=attempts)
        persist_job(folder, job, token)
        return dict(job_id=job_id, kind=kind, space_id=job["target"]["id"],
                    filename=artifact["filename"], file=str(folder / artifact["filename"]),
                    remote_title=Path(artifact["filename"]).stem,
                    representation="imported_document" if artifact["filename"].endswith(".md") else "original_file",
                    remote_key=artifact["remote_key"], sha256=artifact["sha256"],
                    attempt=count + 1, next="native_create_then_exact_lookup_and_read")


def fail(workspace, token, job_id, kind, message):
    required(message, "message")
    if kind not in KINDS:
        raise ValueError("invalid artifact kind")
    with locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        folder, job = load_job(workspace, root, job_id, context)
        job["artifacts"][kind].update(state="failed", last_error=message[:1000])
        job["artifacts"][kind].pop("verified_token", None)
        persist_job(folder, job, token)
        return dict(job_id=job_id, status=job["status"], local_records_untouched=True)


def pending(workspace, token):
    with locked(workspace) as (workspace, root):
        context = load_context(workspace, root, token)
        if not context.get("selected") or context["status"] != "ready":
            return {"jobs": []}
        folder = safe_path(workspace, root / "outbox")
        jobs = []
        if folder.exists():
            for item in sorted(folder.iterdir()):
                if item.is_symlink() or not HEX.fullmatch(item.name) or not item.is_dir():
                    raise ValueError("unexpected outbox entry; preserve for diagnosis")
                path = safe_path(workspace, item / "job.json")
                if not path.exists():
                    continue  # Interrupted snapshot before the manifest committed; prepare can resume it.
                value = read_json(workspace, path)
                if not isinstance(value, dict):
                    raise ValueError("invalid outbox manifest; preserve for diagnosis")
                same_account = (value.get("account_id") == context["account_id"]
                                and (context["account_id"] is not None or value.get("session_id") == context["session_id"]))
                if value.get("target", {}).get("id") != context["selected"]["id"] or not same_account:
                    continue
                _, value = load_job(workspace, root, item.name, context)
                jobs.append(value)
        # A verified alternative encoding of the exact same pair satisfies the
        # transfer; do not keep retrying an unsupported original JSON encoding.
        satisfied = {(x["record_date"], x["version"]) for x in jobs if x["status"] == "synced"}
        return {"jobs": [dict(job_id=x["job_id"], record_date=x["record_date"], status=x["status"])
                         for x in jobs if (x["record_date"], x["version"]) not in satisfied]}


def post_save_handoff(workspace, day):
    """Diagnostic routing hint only; no queue, host detection or network side effects."""
    paths = ensure_workspace(Path(workspace))
    record = json.loads((paths.structured_records / f"{support.valid_date(day)}.json").read_text(encoding="utf-8"))
    return dict(local_save="verified", record_date=day, record_status=record["record_status"],
                sync_next="discover_current_host_spaces" if record["record_status"] == "final" else "not_final")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    discovery = commands.add_parser("discover")
    discovery.add_argument("--host-file", type=Path, required=True)
    for name in ("select", "prepare", "observe", "begin-upload", "uploaded", "fail", "pending"):
        command = commands.add_parser(name)
        command.add_argument("--token", required=True)
        if name == "select":
            command.add_argument("--space-id", required=True)
        elif name == "prepare":
            command.add_argument("--date", required=True)
            command.add_argument("--format", choices=("json", "json-md"), default="json")
        elif name in ("observe", "begin-upload", "uploaded", "fail"):
            command.add_argument("--job", required=True)
            command.add_argument("--kind", choices=KINDS, required=True)
            if name in ("observe", "uploaded"):
                command.add_argument("--evidence-file", type=Path, required=True)
            if name == "fail":
                command.add_argument("--message", required=True)
    args = parser.parse_args()
    try:
        workspace = ensure_workspace(args.workspace).workspace
        if args.command == "discover":
            result = discover(workspace, read_json(workspace, args.host_file))
        elif args.command == "select":
            result = select(workspace, args.token, args.space_id)
        elif args.command == "prepare":
            result = prepare(workspace, args.token, args.date, args.format)
        elif args.command == "observe":
            result = observe(workspace, args.token, args.job, args.kind, read_json(workspace, args.evidence_file))
        elif args.command == "begin-upload":
            result = begin_upload(workspace, args.token, args.job, args.kind)
        elif args.command == "uploaded":
            result = uploaded(workspace, args.token, args.job, args.kind, read_json(workspace, args.evidence_file))
        elif args.command == "fail":
            result = fail(workspace, args.token, args.job, args.kind, args.message)
        else:
            result = pending(workspace, args.token)
        print(encoded(result).decode("utf-8"), end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError, WorkspaceRuntimeError) as error:
        print(json.dumps(dict(status="error", error=str(error), local_records_untouched=True), ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
