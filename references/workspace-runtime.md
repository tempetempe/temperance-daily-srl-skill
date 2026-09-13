# Workspace and Runtime protocol

5.0 uses lazy `.daily-srl/support-events/YYYY-MM-DD.json` for day-scoped support batches, consolidated at closure or explicit pause, not on every reply. Before record updates the helper preserves old bytes under `.daily-srl/record-backups/YYYY-MM-DD/`; same-day lock/pending files support recovery. These are not a third page, workflow manager or long-term preference system. New-day Runtime replacement does not delete them or authorize reading earlier learning/support content. See [support-records.md](support-records.md) for versioned data, rendering and recovery. Workspace layout and page State schemas remain unchanged.

## Authority and location

The Workspace Root is the folder explicitly selected or authorized by the host for the current task. Never infer it from the Skill installation path, a previous conversation, Desktop, Documents, or a disk search. Internal machine data and the single learner-facing output directory are:

```text
<workspace-root>/.daily-srl/
<workspace-root>/Learning Records/
```

The installed Skill package is read-only reusable code and content. It must not receive learner State or Records, runtime patches, caches, logs, or generated helper files.

## Readiness preflight

Normal Skill Entry implicitly runs:

```text
Resolve authorized Workspace Root
→ ensure_workspace()
→ inspect current Runtime
→ continue the current Daily SRL stage
```

There is no learner-facing initialization flow. If the host supplies no writable root, ask the learner only to select or authorize a folder for Daily SRL records.

`scripts/workspace_runtime.py --workspace <root> ensure` is idempotent. On an empty Workspace it creates only:

```text
Learning Records/
└── (empty until Review Checkpoint)
.daily-srl/
├── workspace.json
├── runtime/
└── structured-records/
```

It may restore a missing infrastructure directory. It never creates empty Runtime files, deletes unknown files, clears Records, rewrites learning data, replaces damaged State, or performs an unverified bulk migration.

Directory readiness checks do not call `mkdir` for paths that already exist. Missing components are created one at a time and verified. This preserves idempotence in hosts whose filesystem broker raises `EEXIST` before Python can honor `exist_ok=True`. A formal helper failure is a stop condition: do not create a Workspace-local patch wrapper, monkey-patch `Path.mkdir`, disable the host sandbox, or replace the helper with manual shell operations.

`workspace.json` is only a marker. It contains `workspace_type`, `workspace_version`, `layout_version`, `runtime_version`, a stable random `workspace_id`, and creation/last-use timestamps. Layout 2 places learner-facing Markdown in `Learning Records/`. The marker contains no learning content and no absolute package path.

## Layout migration

An existing marker without `layout_version` is layout 1. If `.daily-srl/learning-records/` contains files, normal `ensure` stops and requires the formal helper:

```bash
python3 <skill-package>/scripts/workspace_runtime.py \
  --workspace <workspace-root> migrate-layout
```

The helper preflights every destination, refuses different-content conflicts, and writes `.daily-srl/migration-pending.json` with Workspace identity and relative file SHA-256 values before copying or retiring anything. It copies through flushed temporary files, verifies before and after install, retires legacy sources only after destinations match, then updates `layout_version` to 2. The pending manifest is removed only after successful completion. New writes use only `Learning Records/`; no hidden Markdown mirror is maintained.

On an ordinary retirement/marker failure it attempts to restore missing legacy files byte-for-byte from verified visible copies, without overwriting conflicts. If restoration also fails or the process is interrupted, the manifest and verified copies remain. A pending manifest blocks normal `ensure` even if the marker already says layout 2. Never delete it manually or infer completion from an empty legacy directory. Re-run only `migrate-layout`: it checks the complete original file/hash set, stops on conflicts/corruption/missing records, and resumes the same migration. `migration-log.jsonl` records failures and retry guidance; after abrupt process exit the pending manifest itself is the interruption evidence. This handles tested process interruption and normal write failures, not a guarantee against filesystem/hardware failure or concurrent migration/editing. Serialize migration with other writes to this Workspace.

## Runtime Pair lifecycle

The two formal pairs are:

```text
assessment.html      ↔ assessment-state.json
daily-page.html      ↔ daily-page-state.json
```

A pair is Ready only when both artifacts exist and State is valid for the requested page. Bootstrap creates neither pair.

Assessment generation starts only on clear Skill Entry. The generator first builds Initial State from the learner's natural-language tasks, including stable Task IDs, then renders the matching HTML and installs State, HTML, and Runtime metadata as one rollback-capable bundle.

Daily Page generation starts only after saved Assessment, necessary Coaching, and clear Generate Daily Page intent. Its Initial State already contains the Assessment linkage, Coaching Result, learner-confirmed Final Plan, empty per-task Review and Task Note fields, and empty Daily Diary. It is installed with the matching HTML in the same way.

The bundle writer stages complete files before replacing destinations. If a normal replacement fails, it restores the previous pair or leaves both absent. `runtime-manifest.json` records the initial-State hash and whether a successful browser save has occurred; it is recovery metadata, not learner content.

Once the learner saves through the page, the State JSON is authoritative. Regenerating HTML must not change State. A same-date full replacement requires an explicit engineering `--force`; normal re-entry recovers or reopens the current pair instead.

Starting a different date may replace fixed Runtime files only. Both Record directories and all of their contents remain untouched.

## Pair recovery matrix

| Observed state | Action | Safety rule |
| --- | --- | --- |
| HTML + valid State | Validate and continue | State is authoritative |
| State only | Re-render HTML from the installed template | Preserve State bytes and learner edits |
| HTML only, manifest proves untouched Initial State | Restore the exact embedded Initial State, then validate | Allowed only when `user_saved` is false and hashes match |
| HTML only, prior interaction possible | Stop as Runtime incomplete | Preserve HTML and metadata; do not invent State |
| Invalid JSON or schema-invalid State | Stop and report the exact path | Preserve damaged bytes; never replace with `{}` or Initial State |
| Missing `runtime/` | Recreate the empty directory | Do not infer or fabricate a flow stage |
| Missing one Records directory | Recreate only that directory | Do not touch the other directory's history |

Run recovery through `scripts/bridge_readiness.py ... ensure` or `workspace_runtime.recover_pair()` before exposing a page. Never manually extract embedded JSON from HTML. Pair incompleteness never means “start today again.” Recovery actions and safety stops are recorded under `.daily-srl/recovery-log.jsonl`.

## Maintenance paths

`workspace_runtime.py --workspace <workspace-root> paths` returns the exact authorized Workspace, internal root, Runtime root, Structured Record root, visible Learning Record root, and metadata paths. It never scans parent, sibling, Desktop, or remembered locations.

## Bridge readiness

For an interactive page, run:

```bash
python3 <skill-package>/scripts/bridge_readiness.py \
  --workspace <workspace-root> ensure --page assessment
```

Use `--page daily-page` for the Daily Page. The helper:

1. ensures the Workspace and conservatively recovers the requested pair;
2. checks saved Bridge metadata and health;
3. reuses a healthy Bridge only when its Workspace ID and canonical root match;
4. otherwise attempts port 8977, falls back to an available dynamic loopback port when occupied, and starts the Bridge;
5. verifies health, Workspace identity, pair status, schema-valid State, HTML access, and the State endpoint;
6. returns JSON containing the verified `page_url` that the host opens.

The HTML uses same-origin relative `/api/...` URLs, so no fixed port or endpoint is embedded. One Bridge serves both pages for one Workspace. Different Workspaces require different verified processes and never share State.

`bridge.json` and `bridge.log` are operational files under `.daily-srl/`. A stale marker is ignored when health and Workspace identity do not match. `bridge_readiness.py ... stop` terminates only the process whose token, root, and Workspace ID still match. Normal learner use does not mention or require Bridge, Python, localhost, endpoints, or ports.

The implementation uses the host's Python runtime to start a loopback subprocess. If a host does not permit subprocess startup, loopback access, or page opening, readiness must stop and report that host limitation; it must never present a manually started Bridge as a successful normal learner path.
