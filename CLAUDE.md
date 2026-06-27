# CLAUDE.md

@AGENTS.md

<!-- AGENTS.md is the source of truth (architecture, non-obvious facts, conventions).
     Anything below is Claude-specific and overrides the imported content on conflict. -->

## Autonomous setup

When asked to set up this repo on a machine, follow the 8-step install in `README.md`. Condensed:

1. Install the upstream skill: `git clone https://github.com/eugeniughelbur/obsidian-second-brain ~/.claude/skills/obsidian-second-brain`, then run its `scripts/setup.sh "<VAULT_PATH>"`.
2. Clone this repo to a stable path (referenced from `settings.json`).
3. Copy the three runtime scripts to `~/.claude/`: `hooks/obsidian-find-hook.py`, `hooks/build_vault_index.py`, `hooks/update-vault-index.sh`.
4. Merge the hook entries from `README.md` into `~/.claude/settings.json`, replacing `<PATH_TO_REPO>`, `<PATH_TO_YOUR_VAULT>`, `<CLAUDE_BINARY>`, and `<SAVE_MODEL>`.
5. Build the index once: `python3 ~/.claude/build_vault_index.py --vault "$OBSIDIAN_VAULT_PATH" --db ~/.claude/vault-index.db`.
6. Requires: Python 3 (stdlib), `jq`, `ollama` + `nomic-embed-text`, and the `claude` CLI.

## Claude-specific rules

- No comments unless the why is non-obvious; no error handling for impossible/internal cases.
- Prefer editing existing files over creating new ones.
- Never use `--dangerously-skip-permissions`; the Stop hook uses `--permission-mode default` + an `--allowedTools` allowlist.
- When adding a feature/fix/tool, update `README.md` and `AGENTS.md` (if architecture changes) before considering the task done.
