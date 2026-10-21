#!/usr/bin/env python3
"""Save factual, day-scoped support events; synchronize existing Review records."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_runtime import ensure_workspace, ensure_directory, WorkspaceRuntimeError
from platform_lock import nonblocking_file_lock

ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
PHASES = {"entry", "planning", "coaching", "execution", "review", "further_discussion", "after_review"}
KINDS = {"preference", "acknowledgement", "proposal", "decision", "attempt", "feedback", "transition", "correction"}
FIELDS = {"event_id", "episode_id", "occurred_at", "phase", "kind", "branch",
          "related_task_ids", "related_event_id", "strategy_ids", "learner_words",
          "agent_action", "learner_feedback", "learner_visible"}
TIME_FIELDS = {"time_source", "time_evidence"}
SUMMARY_FIELDS = {"summary_id", "episode_id", "title", "paragraphs", "event_ids", "supersedes"}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("date must be YYYY-MM-DD")
    return value


def paths_for(workspace, day):
    valid_date(day)
    paths = ensure_workspace(workspace)
    directory = paths.root / "support-events"
    if directory.is_symlink():
        raise ValueError("support directory must not be a symlink")
    ensure_directory(directory)
    targets = (directory / f"{day}.json",
               paths.structured_records / f"{day}.json",
               paths.learning_records / f"{day}.md")
    for target in (*targets, directory / f"{day}.pending.json", directory / f"{day}.lock"):
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError(f"refusing symlink: {target}")
    return paths, targets


@contextmanager
def day_lock(workspace, day, *, recovering=False):
    paths, targets = paths_for(workspace, day)
    lock = targets[0].with_suffix(".lock")
    with nonblocking_file_lock(lock, busy_message="another writer is saving this date; retry after it finishes"):
        if not recovering and targets[0].with_suffix(".pending.json").exists():
            raise ValueError("interrupted record transaction; use support_events.py recover for this date")
        yield paths, targets


def text(value, field, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{field} must be {'a' if empty else 'a nonempty'} string")
    return value  # Preserve learner wording exactly.


def strings(value, field):
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return [text(item, field) for item in value]


def validate_event(value):
    if not isinstance(value, dict) or set(value) not in (FIELDS, FIELDS | TIME_FIELDS, FIELDS | TIME_FIELDS | {"recorded_at"}):
        raise ValueError("event fields must match support-records.md exactly")
    for key in ("event_id", "episode_id"):
        if not isinstance(value[key], str) or not ID.fullmatch(value[key]):
            raise ValueError(f"invalid {key}")
    if "time_source" in value:
        if value["time_source"] not in ("unknown", "learner_report", "activity_timer"):
            raise ValueError("invalid time_source; file/chat timestamps are not activity timing")
        text(value["time_evidence"], "time_evidence", empty=True)
        if value["time_source"] == "unknown":
            if value["occurred_at"] is not None or value["time_evidence"]:
                raise ValueError("unknown event time requires null occurred_at and empty evidence")
        elif value["occurred_at"] is None or not value["time_evidence"].strip():
            raise ValueError("known event time requires a timestamp and activity evidence")
    if value["occurred_at"] is not None:
        validate_stamp(value["occurred_at"], "occurred_at")
    elif "time_source" not in value:
        raise ValueError("legacy event requires a timestamp")
    if "recorded_at" in value:
        validate_stamp(value["recorded_at"], "recorded_at")
    if not isinstance(value["phase"], str) or not isinstance(value["kind"], str) or value["phase"] not in PHASES or value["kind"] not in KINDS:
        raise ValueError("invalid phase or kind")
    if not isinstance(value["branch"], str) or value["branch"] not in {"undecided", "learning", "emotional_support"}:
        raise ValueError("invalid branch")
    for key in ("learner_words", "agent_action", "learner_feedback"):
        text(value[key], key, empty=True)
    if not any(value[key].strip() for key in ("learner_words", "agent_action", "learner_feedback")):
        raise ValueError("event needs an observable statement or action")
    for key in ("related_task_ids", "strategy_ids", "learner_visible"):
        strings(value[key], key)
    if len(set(value["related_task_ids"])) != len(value["related_task_ids"]):
        raise ValueError("duplicate task association")
    if any(not re.fullmatch(r"ER-(0[1-9]|1[0-2])", item) for item in value["strategy_ids"]):
        raise ValueError("unknown strategy ID")
    ref = value["related_event_id"]
    if ref is not None and (not isinstance(ref, str) or not ID.fullmatch(ref)):
        raise ValueError("invalid related_event_id")
    if not value["learner_visible"]:
        raise ValueError("event needs a factual learner-facing rendering")
    # Defense against known internal identifiers, not a semantic quality classifier.
    if any(re.search(r"ER-\d+|\b(?:HIGH|MEDIUM|LOW|CONTROLLABLE|UNCONTROLLABLE|MIXED|schema_version|event_id)\b", item)
           or "<!--" in item for item in value["learner_visible"]):
        raise ValueError("internal identifiers must not appear in learner_visible")
    return value


def validate_stamp(value, field):
    stamp = datetime.fromisoformat(text(value, field).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"{field} requires a timezone")


def validate_summary(value):
    if not isinstance(value, dict) or set(value) not in (SUMMARY_FIELDS, SUMMARY_FIELDS | {"recorded_at"}):
        raise ValueError("summary fields do not match contract")
    for key in ("summary_id", "episode_id"):
        if not isinstance(value[key], str) or not ID.fullmatch(value[key]):
            raise ValueError(f"invalid {key}")
    text(value["title"], "title")
    strings(value["paragraphs"], "paragraphs")
    strings(value["event_ids"], "event_ids")
    if not value["paragraphs"] or not value["event_ids"] or len(set(value["event_ids"])) != len(value["event_ids"]):
        raise ValueError("summary needs paragraphs and unique evidence event_ids")
    if value["supersedes"] is not None and (not isinstance(value["supersedes"], str) or not ID.fullmatch(value["supersedes"])):
        raise ValueError("invalid supersedes")
    # Reuse the small internal-language guard, not a truthfulness classifier.
    probe = dict(event_id="probe", episode_id="probe", occurred_at=None, time_source="unknown", time_evidence="",
                 phase="after_review", kind="acknowledgement", branch="emotional_support", related_task_ids=[],
                 related_event_id=None, strategy_ids=[], learner_words="", agent_action="render validation",
                 learner_feedback="", learner_visible=[value["title"], *value["paragraphs"]])
    validate_event(probe)
    if "\n" in value["title"] or "\r" in value["title"]:
        raise ValueError("summary title must be one line")
    if "recorded_at" in value:
        validate_stamp(value["recorded_at"], "recorded_at")


def load_journal(workspace, day):
    _, targets = paths_for(workspace, day)
    if not targets[0].exists():
        return {"schema_version": 2, "record_date": day, "events": [], "summaries": []}
    value = json.loads(targets[0].read_text(encoding="utf-8"))
    validate_journal(value, day)
    return value


def validate_journal(value, day):
    if not isinstance(value, dict) or value.get("schema_version") not in (1, 2):
        raise ValueError("invalid support journal version")
    fields = {"schema_version", "record_date", "events"} | ({"summaries"} if value["schema_version"] == 2 else set())
    if set(value) != fields or value["record_date"] != day or not isinstance(value["events"], list):
        raise ValueError("invalid support journal date/schema")
    seen = {}
    for event in value["events"]:
        validate_event(event)
        if event["event_id"] in seen:
            raise ValueError("duplicate stored event")
        ref = event["related_event_id"]
        if ref is not None and (ref not in seen or seen[ref]["episode_id"] != event["episode_id"]):
            raise ValueError("event reference must point to an earlier event in the same episode")
        seen[event["event_id"]] = event
    summaries = value.get("summaries", [])
    if not isinstance(summaries, list):
        raise ValueError("summaries must be an array")
    latest, ids = {}, set()
    for summary in summaries:
        validate_summary(summary)
        episode = summary["episode_id"]
        if summary["summary_id"] in ids or summary["supersedes"] != latest.get(episode):
            raise ValueError("summary revision must supersede the current same-episode summary")
        if any(e not in seen or seen[e]["episode_id"] != episode for e in summary["event_ids"]):
            raise ValueError("summary evidence must reference same-episode saved events")
        latest[episode] = summary["summary_id"]
        ids.add(summary["summary_id"])
    by_id = {s["summary_id"]: s for s in summaries}
    for episode, summary_id in latest.items():
        expected = [e["event_id"] for e in value["events"] if e["episode_id"] == episode]
        if by_id[summary_id]["event_ids"] != expected:
            raise ValueError("latest summary must cite all same-episode facts in stored order")
    return value


def load_events(workspace, day):
    return load_journal(workspace, day)["events"]


def render_event(event):
    # Legacy v1 layout recognition only; new outputs use render_support summaries.
    body = "\n\n".join("\n".join("> " + line for line in item.splitlines()) for item in event["learner_visible"])
    return f"\n\n### 本次支持\n\n{body}\n"


def projection(events):
    return {"schema_version": 1, "events": events}


def journal_projection(journal):
    return {k: copy.deepcopy(v) for k, v in journal.items() if k != "record_date"}


def render_support(value):
    events = value.get("events", [])
    if not events:
        return ""
    latest = {s["episode_id"]: s for s in value.get("summaries", [])}
    episodes = list(dict.fromkeys(e["episode_id"] for e in events))
    parts = ["## 情绪方面的支持"]
    for n, episode in enumerate(episodes, 1):
        summary = latest.get(episode)
        if summary:
            title, paragraphs = summary["title"], summary["paragraphs"]
        else:  # Legacy facts remain readable; do not invent a new semantic summary.
            title = f"情绪支持片段 {n}"
            paragraphs = [p for e in events if e["episode_id"] == episode for p in e["learner_visible"]]
        if len(episodes) > 1:
            parts.append(f"### {title}")
        parts.extend("\n".join("> " + line for line in p.splitlines()) for p in paragraphs)
    return "\n\n".join(parts) + "\n"


def check_projection(record, learning, events, journal=None):
    from review_records import last_learning_status, verify_render_integrity, legacy_render_matches
    if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("record_status") not in ("review_saved", "final"):
        raise ValueError("invalid existing Structured Record")
    if last_learning_status(learning) != record["record_status"]:
        raise ValueError("record status mismatch")
    if not isinstance(record.get("review_checkpoint"), dict) or not isinstance(record["review_checkpoint"].get("tasks"), list):
        raise ValueError("invalid Review checkpoint")
    stored = record.get("emotional_support", {"schema_version": 1, "events": []})
    expected = journal_projection(journal) if journal is not None else projection(events)
    if not events and "emotional_support" not in record:
        expected = stored
    if stored != expected:
        raise ValueError("support journal and Structured Record disagree; preserve for diagnosis")
    if record.get("render_version") == 2:
        verify_render_integrity(record, learning)
    elif record.get("render_version") is not None or not legacy_render_matches(record, learning, events):
        raise ValueError("legacy record content differs from supported renderer; preserve for diagnosis")


def current_task_ids(paths, day, record=None):
    if record is not None:
        return {item["task_id"] for item in record["review_checkpoint"]["tasks"]}
    path = paths.runtime / "assessment-state.json"
    if not path.exists():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("assessment_date") != day:
        return set()
    from persistence_bridge import validate_assessment_state
    return {item["task_id"] for item in validate_assessment_state(value, mark_saved=False)["tasks"]}


def commit(workspace, day, outputs, expected_before=None):
    """Durable intent plus existing rollback; recover only recognized before/after bytes."""
    from review_records import stage_text, replace_pair
    paths, targets = paths_for(workspace, day)
    if not set(outputs) <= set(targets):
        raise ValueError("transaction target outside this date's records")
    originals = {path: path.read_bytes() if path.exists() else None for path in outputs}
    if expected_before is not None and any(originals[p] != expected_before[p] for p in outputs):
        raise ValueError("record changed while preparing update; preserve user edits")
    # A recoverable, content-addressed pre-update copy; never overwrite an archive.
    if any(value is not None for value in originals.values()):
        backup_root = paths.root / "record-backups"
        backup_dir = backup_root / day
        for directory in (backup_root, backup_dir):
            if directory.is_symlink():
                raise ValueError("backup directory must not be a symlink")
            ensure_directory(directory)
        backup = encode({str(p.relative_to(paths.workspace)): b.decode("utf-8") if b is not None else None
                         for p, b in originals.items()})
        destination = backup_dir / f"{digest(backup.encode('utf-8'))}.json"
        if destination.is_symlink():
            raise ValueError("backup must not be a symlink")
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(backup)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if destination.read_text(encoding="utf-8") != backup:
                raise ValueError("backup checksum mismatch")
    pending = targets[0].with_suffix(".pending.json")
    entries = []
    for path, content in outputs.items():
        entries.append({"path": str(path.relative_to(paths.workspace)),
                        "before": digest(path.read_bytes()) if path.exists() else None,
                        "after": content, "sha256": digest(content.encode("utf-8"))})
    intent = {"schema_version": 1, "record_date": day, "entries": entries}
    staged = stage_text(pending, encode(intent))
    try:
        os.replace(staged, pending)
        if any((p.read_bytes() if p.exists() else None) != originals[p] for p in outputs):
            raise ValueError("record changed before commit; preserve for diagnosis")
        replace_pair(outputs)
        if any(p.read_bytes() != content.encode("utf-8") for p, content in outputs.items()):
            raise ValueError("written output did not match transaction; preserve pending intent")
    except BaseException:
        # Ordinary rollback complete: remove intent. Otherwise leave it for explicit recovery.
        if all((digest(path.read_bytes()) if path.exists() else None) == entry["before"]
               for path, entry in zip(outputs, entries)):
            pending.unlink(missing_ok=True)
        raise
    else:
        pending.unlink()
    finally:
        staged.unlink(missing_ok=True)


def recover(workspace, day):
    from review_records import replace_pair
    with day_lock(workspace, day, recovering=True) as (paths, targets):
        pending = targets[0].with_suffix(".pending.json")
        if not pending.exists():
            return {"recovered": False}
        intent = json.loads(pending.read_text(encoding="utf-8"))
        if not isinstance(intent, dict) or intent.get("schema_version") != 1 or intent.get("record_date") != day:
            raise ValueError("invalid recovery intent")
        entries = intent.get("entries")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 3:
            raise ValueError("invalid recovery entries")
        outputs = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "before", "after", "sha256"}:
                raise ValueError("invalid recovery entry")
            if not isinstance(entry["path"], str) or Path(entry["path"]).is_absolute() or ".." in Path(entry["path"]).parts:
                raise ValueError("invalid recovery path")
            path = paths.workspace / entry["path"]
            if path not in targets or path in outputs or path.is_symlink():
                raise ValueError("invalid recovery target")
            content = text(entry["after"], "after")
            if digest(content.encode("utf-8")) != entry["sha256"]:
                raise ValueError("recovery payload checksum mismatch")
            actual = digest(path.read_bytes()) if path.exists() else None
            if actual not in (entry["before"], entry["sha256"]):
                raise ValueError("file changed since interrupted write; preserve for diagnosis")
            outputs[path] = content
        replace_pair(outputs)
        pending.unlink()
        return {"recovered": True, "record_date": day}


def normalize_batch_input(batch):
    """Fill only mechanical defaults; preserve supplied facts, IDs and invalid values.

    Compact input is an input convenience, not a new stored journal schema.
    Time is unknown only when its entire triple is omitted. Partial or explicit
    timing continues through the strict validator without invented evidence.
    """
    if not isinstance(batch, dict):
        return batch  # Existing validation supplies the contract error.
    result = copy.deepcopy(batch)
    if isinstance(result.get("events"), list):
        for item in result["events"]:
            if not isinstance(item, dict):
                continue
            if not ({"occurred_at"} | TIME_FIELDS) & item.keys():
                item.update(occurred_at=None, time_source="unknown", time_evidence="")
            for field, default in (("related_task_ids", []), ("related_event_id", None), ("strategy_ids", [])):
                if field not in item:
                    item[field] = copy.deepcopy(default)
    if isinstance(result.get("summaries"), list):
        for item in result["summaries"]:
            if isinstance(item, dict) and "supersedes" not in item:
                item["supersedes"] = None
    return result


def merge_batch(journal, batch, task_ids, *, legacy=False):
    if not legacy:
        batch = normalize_batch_input(batch)
    if not isinstance(batch, dict) or set(batch) != {"schema_version", "events", "summaries"} or batch["schema_version"] != 2:
        raise ValueError("batch requires schema_version 2, events and summaries")
    if not isinstance(batch["events"], list) or not isinstance(batch["summaries"], list):
        raise ValueError("batch events/summaries must be arrays")
    result = copy.deepcopy(journal)
    result["schema_version"] = 2
    result.setdefault("summaries", [])
    touched = set()
    for field, id_key, validator in (("events", "event_id", validate_event), ("summaries", "summary_id", validate_summary)):
        incoming_ids = set()
        for item in batch[field]:
            validator(item)
            if "recorded_at" in item:
                raise ValueError("recorded_at is generated by the program, not supplied by Agent")
            if not legacy and field == "events" and set(item) != FIELDS | TIME_FIELDS:
                raise ValueError("new events require explicit time_source/time_evidence")
            if item[id_key] in incoming_ids:
                raise ValueError("duplicate ID inside batch")
            incoming_ids.add(item[id_key])
            old = next((e for e in result[field] if e[id_key] == item[id_key]), None)
            if old:
                if {k: v for k, v in old.items() if k != "recorded_at"} != item:
                    raise ValueError("existing ID has different facts; add a sourced correction/revision")
                continue
            if field == "events":
                if not set(item["related_task_ids"]) <= task_ids:
                    raise ValueError("unknown task association for this date")
                touched.add(item["episode_id"])
            saved = copy.deepcopy(item)
            if not legacy:
                saved["recorded_at"] = utc_now()
            result[field].append(saved)
    validate_journal(result, journal["record_date"])
    latest = {s["episode_id"]: s for s in result["summaries"]}
    for episode in touched:
        if legacy and episode not in latest:
            continue
        expected = [e["event_id"] for e in result["events"] if e["episode_id"] == episode]
        if episode not in latest or latest[episode]["event_ids"] != expected:
            raise ValueError("each updated episode needs a consolidated summary citing all its events in order")
    # A legacy caller without summaries keeps the original v1 data shape.
    if legacy and not result["summaries"]:
        result.pop("summaries")
        result["schema_version"] = 1
    return result


def read_pair(targets, journal, day):
    _, structured, readable = targets
    if structured.exists() != readable.exists():
        raise ValueError("paired records incomplete; preserve for diagnosis")
    if not structured.exists():
        return None
    record = json.loads(structured.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("record_date") != day:
        raise ValueError("record date mismatch")
    check_projection(record, readable.read_text(encoding="utf-8"), journal["events"], journal)
    return record


def save_batch(workspace, day, batch, *, legacy=False):
    from review_records import seal_record
    with day_lock(workspace, day) as (paths, targets):
        before = {p: p.read_bytes() if p.exists() else None for p in targets}
        journal = load_journal(workspace, day)
        record = read_pair(targets, journal, day)
        merged = merge_batch(journal, batch, current_task_ids(paths, day, record), legacy=legacy)
        duplicate = merged == journal
        if not duplicate:
            outputs = {targets[0]: encode(merged)}
            if record is not None:
                record["emotional_support"] = journal_projection(merged)
                learning = seal_record(record)
                outputs[targets[1]] = encode(record)
                outputs[targets[2]] = learning
            commit(workspace, day, outputs, expected_before=before)
        result = verify_unlocked(workspace, day)
        return {**result, "saved": True, "duplicate": duplicate}


def save_event(workspace, day, event):
    """Compatibility API only. Normal 5.0 support uses one closing batch."""
    return save_batch(workspace, day, {"schema_version": 2, "events": [event], "summaries": []}, legacy=True)


def verify(workspace, day):
    with day_lock(workspace, day):
        return verify_unlocked(workspace, day)


def verify_unlocked(workspace, day):
    _, targets = paths_for(workspace, day)
    journal = load_journal(workspace, day)
    record = read_pair(targets, journal, day)
    return {"verified": True, "record_date": day, "event_count": len(journal["events"]),
            "formal_review_exists": record is not None, "record_status": record["record_status"] if record else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--date", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("save")
    save.add_argument("--event-file", type=Path, required=True)
    batch = sub.add_parser("save-batch", help="consolidate support at closure or explicit pause")
    batch.add_argument("--batch-file", type=Path, required=True)
    sub.add_parser("show")
    sub.add_parser("verify")
    sub.add_parser("recover")
    args = parser.parse_args()
    try:
        if args.command == "save":
            result = save_event(args.workspace, args.date, json.loads(args.event_file.read_text(encoding="utf-8")))
        elif args.command == "save-batch":
            result = save_batch(args.workspace, args.date, json.loads(args.batch_file.read_text(encoding="utf-8")))
        elif args.command == "recover":
            result = recover(args.workspace, args.date)
        elif args.command == "verify":
            result = verify(args.workspace, args.date)
        else:
            with day_lock(args.workspace, args.date):
                result = load_journal(args.workspace, args.date)
    except (OSError, ValueError, KeyError, TypeError, ImportError, WorkspaceRuntimeError) as error:
        raise SystemExit(f"error: {error}") from error
    if args.command in {"save", "save-batch"} and result.get("record_status") == "final":
        result["team_space_sync"] = {"sync_next": "discover_current_host_spaces", "record_date": args.date}
    print(encode(result), end="")


if __name__ == "__main__":
    main()
