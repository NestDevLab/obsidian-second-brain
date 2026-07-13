# AGENTS.md

## What this repo is

A Claude Code hook layer that wires the [obsidian-second-brain skill](https://github.com/eugeniughelbur/obsidian-second-brain) into every session automatically. This repo is **pure config/docs** — no app code, no build, no tests, no CI.

The upstream skill at `~/.claude/skills/obsidian-second-brain/` provides the slash commands; this repo provides the hooks and `settings.json`.

## Commands

There is no build, test, or lint. `README.md` is the only source file maintained here (the hooks in `hooks/` are reference copies for setup, not called from the repo at runtime).

```
# Verify the README renders (open in browser or markdown preview)
# No automated verification steps exist
```

## Architecture

```
hooks/
  load_vault_context.py   → SessionStart: injects _CLAUDE.md
  obsidian-find-hook.py   → UserPromptSubmit: vector search via ollama
  build_vault_index.py    → one-shot/Stop: builds vault-index.db
  update-vault-index.sh   → Stop: thin wrapper, calls build_vault_index.py --incremental
  obsidian-bg-agent.sh    → PostCompact: headless agent, dual-gate opt-in
  validate-ai-first.sh    → PostToolUse(Write|Edit): AI-first rule enforcement
  *.hook.yaml             → platform-neutral hook specs
  postcompact.hook.example.json → ready-to-paste JSON snippet
```

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
- **ollama** + `nomic-embed-text` model — vector search (falls back to grep if absent)
- **Claude Code** CLI (`claude`) — the `Stop` hook and `PostCompact` agent

### Opt-in gates
`obsidian-bg-agent.sh` is **inert by default** — requires BOTH:
- `OBSIDIAN_VAULT_PATH` set
- `OBSIDIAN_BG_AGENT_ENABLED=1`

The setup script sets the first but never the second. No vault writes happen unattended without deliberate opt-in.

### Hardcoded defaults to watch
- `hooks/update-vault-index.sh` line 6 — vault path defaults to `/Users/guido.dilauro/WORKDIR/WORK-WIKI` (overridden by env var at runtime)
- `hooks/build_vault_index.py` line 17 — same pattern

### Stop hook reference
The Stop hook's auto-save command references `SKILL.md` (not `obsidian-second-brain.md`). This was fixed in v1.1.0.
In v1.2.0 the command gained a `--model <SAVE_MODEL>` flag (with a cost note recommending a cheap model tier) so the per-turn save agent doesn't run on the default session model.
In v1.3.0 the command gained a `--name 'obsidian-save (bg)'` flag so the spawned background save agent is identifiable in session/process listings, distinct from the interactive session (cosmetic).

### Repository origin
This repo moved from `guidodl/obsidian-second-brain` to `NestDevLab/obsidian-second-brain`. The old URL redirects.

## Conventions

- Version tags: semver (`v1.0.0`, `v1.1.0`, `v1.2.0`) with matching GitHub Releases
- Branch naming: loose — `feat/*`, `Fix-*`, `fix/*` seen in history
- PRs merge to `main`, no branch protection rules
- No `CLAUDE.md` or other AI instruction file exists in this repo
