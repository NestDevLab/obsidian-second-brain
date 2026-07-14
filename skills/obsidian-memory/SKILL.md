---
name: obsidian-memory
description: Operate the Obsidian standalone or Agent Memory Fabric document client: scan, drain, check health, search, propose, and manage selected PAM projections.
metadata:
  author: Yehonal
  version: "0.3"
---

# Obsidian memory

Run from this skill directory:

```bash
scripts/obsidian-memory <command> [options]
```

Use an explicit vault and vault ID from the user or environment; never guess either. Services load
bearers from the owner-only regular file in `OBSIDIAN_AMF_TOKEN_FILE`; direct `OBSIDIAN_AMF_TOKEN`
remains available for interactive use. The file takes precedence when both are set. Never print or
persist either value. Active/shadow recall should use an owner-only
actor key ring in `OBSIDIAN_AMF_CONTEXT_KEY_RING` plus `OBSIDIAN_AMF_POLICY_REVISION`; the client
then issues a short-lived token bound to each exact request. A literal `OBSIDIAN_AMF_CONTEXT_TOKEN`
is only valid for its original one-shot query and must never be reused as durable configuration.

## Operations

- `client-metadata`: emit location-independent client version, capabilities, scheduled modes, and
  a deterministic digest over every installable Python source file.
- `client-source`: resolve the Agentwheel-installed client asset relative to this skill and emit
  its runtime source root alongside the same metadata. AMF installers must verify the file manifest
  and digest before copying; they must not assume a Codex, Claude, OpenClaw, or Hermes path.
- `status`: report cursor, rejected files, pending/retrying/quarantined outbox counts, mode, and health.
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
outbox counts where applicable; rejected files, retries, or quarantines are degraded, and a scan is
not successful delivery while events remain.

Agentwheel may install this skill and its client asset into Codex, Claude, OpenClaw, and Hermes.
Vitae is intentionally outside this integration profile. Manual operations support `standalone`,
`shadow`, and `active`; an AMF-managed scheduled poller is permitted only in `shadow` mode.
