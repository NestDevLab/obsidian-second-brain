# AGENTS.md

## What this repo is

A portable hook and client layer that wires the [obsidian-second-brain skill](https://github.com/eugeniughelbur/obsidian-second-brain) into agent sessions. It also contains an optional, dependency-free Agent Memory Fabric (AMF) document bridge.

The upstream skill at `~/.claude/skills/obsidian-second-brain/` provides the slash commands. This repo provides hooks, portable instructions, and the standalone/AMF adapter without vendoring the upstream skill.

## Commands

```
python3 -m unittest discover -s tests -v
python3 -m obsidian_amf --help
```

## Architecture

```
hooks/
  load_vault_context.py   → SessionStart: injects _CLAUDE.md
  obsidian-find-hook.py   → UserPromptSubmit: vector search via ollama
  build_vault_index.py    → one-shot/Stop: builds vault-index.db
  update-vault-index.sh   → Stop: thin wrapper, calls build_vault_index.py --incremental
  obsidian-bg-agent.sh    → PostCompact: headless agent, dual-gate opt-in, allowlisted tools
  validate-ai-first.sh    → PostToolUse(Write|Edit): AI-first rule enforcement
  *.hook.yaml             → platform-neutral hook specs
  postcompact.hook.example.json → ready-to-paste JSON snippet
obsidian_amf/
  bridge.py                     → revisioned scanner, outbox, providers, health
  context_signer.py             → short-lived tokens bound to exact recall requests
  projections.py                → explicit managed PAM-to-vault projections
  mcp_server.py                 → governed stdio MCP adapter (amf_search/status/propose)
  __main__.py                   → standalone CLI
tests/
  test_obsidian_amf_bridge.py   → deterministic lifecycle and outage tests
```

The bridge only reads Markdown from the vault. Its cursor/outbox and direct
SQLite corpus are runtime state under `.amf/` by default and must not be
committed. `standalone` owns direct SQLite, `active` delivers to AMF, and
`shadow` keeps direct SQLite authoritative while AMF delivery is observed
independently.

AMF contextual recall never relies on a reusable static context token. An
actor-specific owner-only key ring signs each exact query locally; the bearer,
policy revision, scopes and vault ACL remain independently enforced by AMF.

Projection writes are the only AMF bridge operation that changes the vault.
They require an explicit `project` command, accept active plaintext PAM records
only, and write exclusively under the managed `.amf/records/` namespace using
directory-relative no-follow operations. The scanner excludes that namespace,
preventing projection feedback loops.

## Non-obvious facts

### Runtime vs repo layout
Three scripts are **copied** to `~/.claude/` during setup and run from there at hook fire time:
- `hooks/obsidian-find-hook.py` → `~/.claude/obsidian-find-hook.py`
- `hooks/build_vault_index.py`  → `~/.claude/build_vault_index.py`
- `hooks/update-vault-index.sh` → `~/.claude/update-vault-index.sh`

The other two (`load_vault_context.py`, `validate-ai-first.sh`) run from their repo path.

### Update helper
`update-obsidian-skill.sh` (repo root) updates the upstream skill at `~/.claude/skills/obsidian-second-brain` while preserving local overrides: it stashes local changes, fast-forwards to the latest release (resolved via `gh release view`, because the upstream repo mixes two tag schemes and version-sorting picks the wrong tag), then re-applies the overrides. Added in v1.4.0. Prefer it over the upstream `update.sh`, whose bare `git pull` conflicts on any local override.

### Dependencies
- **Python 3 (stdlib only)** + **jq** — all hook scripts
- **Python 3.10+ stdlib** — AMF bridge and tests
- **ollama** + `nomic-embed-text` model — vector search (falls back to grep if absent)
- **Claude Code** CLI (`claude`) — the `Stop` hook and `PostCompact` agent

### Opt-in gates
`obsidian-bg-agent.sh` is **inert by default** — requires BOTH:
- `OBSIDIAN_VAULT_PATH` set
- `OBSIDIAN_BG_AGENT_ENABLED=1`

The setup script sets the first but never the second. No vault writes happen unattended without deliberate opt-in. When enabled, the agent runs with `--permission-mode default` + an `--allowedTools` allowlist (file tools + `Bash(mkdir *)`), never `--dangerously-skip-permissions`.

### PostCompact bg-agent reference
Until v1.8.0 the bg-agent invoked `claude --dangerously-skip-permissions` and was inert-by-default for that reason. v1.9.0 hardened it to the Stop-hook pattern (`--permission-mode default` + `--allowedTools`, plus `--name 'obsidian-bg-agent (bg)'`), keeping the dual-gate opt-in. `tests/test_no_dangerous_flags.py` guards the regression.

### MCP adapter
`mcp_server.py` (v1.9.0) wraps the bridge as an MCP stdio server so MCP-native agents (Claude Desktop, OpenCode, Cursor, Cline) can query the fabric. It always runs `active` mode and takes one actor's credentials from `OBSIDIAN_AMF_*` env — governance (scopes, vault ACLs, policy revision) is enforced by AMF server-side, never by the adapter. Each harness runs its own adapter process with its own actor identity; there is no shared token. Tools: `amf_search`, `amf_status`, `amf_propose`. Tests: `tests/test_obsidian_amf_mcp.py`. Full guide: `docs/mcp-adapter.md`.

### Plugin installs register hooks too
Installed as a Claude Code plugin, the upstream skill registers `SessionStart`, `PostToolUse` (its own `validate-ai-first.sh`) and `PostCompact` (upstream bg-agent, `--dangerously-skip-permissions`) through its `hooks/hooks.json`. Adding the same events from README Step 6 makes them fire twice, so plugin users should only add `UserPromptSubmit` and `Stop`. Plugin hooks can't be disabled one at a time from `settings.json`.

`validate-ai-first.sh` exits 2 on warnings, because Claude Code passes `PostToolUse` stderr to Claude only on exit 2. Exit 1 shows the warning to the user and Claude never sees it.

### Hardcoded defaults to watch
- `hooks/update-vault-index.sh` line 6 — vault path defaults to `/Users/guido.dilauro/WORKDIR/WORK-WIKI` (overridden by env var at runtime)
- `hooks/build_vault_index.py` line 17 — same pattern

### Vector index format
`build_vault_index.py` stores unit-normalized float32 blobs (`array('f')`), and `obsidian-find-hook.py` scores with a plain dot product. The two must change together. `CREATE TABLE IF NOT EXISTS` never migrates an old JSON-text table, and `--incremental` skips unchanged notes, so an upgraded install needs one full (non-incremental) rebuild.

The find hook uses two cutoffs: `OBSIDIAN_FIND_MIN_SCORE` (0.72) for ambient prompts and `OBSIDIAN_FIND_EXPLICIT_MIN_SCORE` (0.45) for prompts that hit the `VAULT_INTENT` keywords. A single cutoff can't work, because `nomic-embed-text` scores almost all prose at 0.63–0.73.

### Stop hook reference
The Stop hook's auto-save command references `SKILL.md` (not `obsidian-second-brain.md`). This was fixed in v1.1.0.
In v1.2.0 the command gained a `--model <SAVE_MODEL>` flag (with a cost note recommending a cheap model tier) so the per-turn save agent doesn't run on the default session model.
In v1.3.0 the command gained a `--name 'obsidian-save (bg)'` flag so the spawned background save agent is identifiable in session/process listings, distinct from the interactive session (cosmetic).
The command is prefixed with `[ -n "$OBSIDIAN_SAVE_BG" ] || OBSIDIAN_SAVE_BG=1`. The save agent's own Stop event re-fires the hook, and without that guard it keeps spawning saves of saves.

### Repository origin
This repo moved from `guidodl/obsidian-second-brain` to `NestDevLab/obsidian-second-brain`. The old URL redirects.

## Conventions

- Version tags: semver (`v1.0.0`, `v1.1.0`, `v1.2.0`) with matching GitHub Releases
- Branch naming: loose — `feat/*`, `Fix-*`, `fix/*` seen in history
- PRs merge to `main`, no branch protection rules
- `AGENTS.md` is the shared source of truth; `CLAUDE.md` adds Claude-specific setup rules
