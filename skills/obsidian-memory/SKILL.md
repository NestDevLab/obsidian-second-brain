---
name: obsidian-memory
description: Operate the Obsidian standalone or Agent Memory Fabric document client: scan, drain, check health, search, propose, and manage selected PAM projections.
metadata:
  author: Yehonal
  version: "0.1"
---

# Obsidian memory

Run from this skill directory:

```bash
scripts/obsidian-memory <command> [options]
```

Use an explicit vault and vault ID from the user or environment; never guess either. Tokens belong
in `OBSIDIAN_AMF_TOKEN` and `OBSIDIAN_AMF_CONTEXT_TOKEN`: do not print or persist them.

## Operations

- `status`: report cursor, pending/retrying outbox counts, mode, and health.
- `scan`: read Markdown, record revisions/tombstones, then drain; use `--no-drain` only for a
  deliberate capture-only run. AMF outage must leave events queued, not block vault work.
- `drain`: retry the durable outbox without rescanning.
- `search`: direct SQLite in `standalone`; combined AMF recall in `active`; direct-authoritative
  output plus AMF diagnostics in `shadow`. Report degraded shadow diagnostics without discarding
  the direct result.
- `propose`: requires an explicit complete PAM-compatible JSON input and idempotency key; it queues
  curation and never writes canonical memory.
- `project`/`unproject`: require explicit selection because they mutate the managed
  `.amf/records/` namespace. Only active plaintext PAM records are projectable; sealed claims stay
  sealed. Managed projections are not canonical and must not be edited as such.

Before a mutating operation, state the mode and target vault. Finish with the command result plus
pending/retrying counts where applicable; never call a scan successful delivery when events remain.
