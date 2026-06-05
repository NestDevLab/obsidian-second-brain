# obsidian-second-brain

My personal configuration layer — hooks and Claude Code settings — built on top of the [obsidian-second-brain skill](https://github.com/eugeniughelbur/obsidian-second-brain) by [@eugeniughelbur](https://github.com/eugeniughelbur).

## How it works

The core skill lives at `~/.claude/skills/obsidian-second-brain/` and is installed via the upstream repo's [install instructions](https://github.com/eugeniughelbur/obsidian-second-brain#install). It provides 40+ slash commands (`/obsidian-save`, `/obsidian-daily`, `/obsidian-ingest`, etc.) that Claude Code can invoke to read and write an Obsidian vault.

This repo captures the **glue layer**: the Claude Code hooks and `settings.json` config that wire the skill into every session automatically, without needing to type a command. Here's the flow:

```
Every session start
  └── SessionStart hook → load_vault_context.py
        Reads _CLAUDE.md from the vault, injects it as context
        so Claude knows vault structure, folder map, and rules.

Every prompt
  └── UserPromptSubmit hook → obsidian-find-hook.py
        Embeds the prompt via ollama (nomic-embed-text),
        runs cosine similarity against ~/.claude/vault-index.db,
        injects the top 5 matching note snippets as context.
        Falls back to grep if the index doesn't exist.

Every vault write (Write/Edit tool)
  └── PostToolUse hook → validate-ai-first.sh
        Checks frontmatter, "For future Claude" preamble,
        required fields. Warns Claude to self-correct if missing.

After context compaction
  └── PostCompact hook → obsidian-bg-agent.sh
        Spawns a headless Claude agent that reads the compaction
        summary and propagates decisions/tasks/people to the vault.

End of session
  └── Stop hook (1) → headless claude -p "/obsidian-save"
        Auto-saves everything vault-worthy from the conversation.
  └── Stop hook (2) → update-vault-index.sh
        Incrementally re-indexes any vault notes changed this session.
```

## What's in here

### Hooks

| File | Trigger | What it does |
|---|---|---|
| `hooks/load_vault_context.py` | `SessionStart` | Reads `_CLAUDE.md` from the vault and injects it into every session as context. Requires `OBSIDIAN_VAULT_PATH` env var. |
| `hooks/obsidian-find-hook.py` | `UserPromptSubmit` | Embeds each prompt via ollama, runs cosine similarity against the vault index DB, injects top 5 matching note snippets as context. Falls back to grep if index is absent. |
| `hooks/build_vault_index.py` | (one-shot / Stop) | Builds or rebuilds `~/.claude/vault-index.db` — a SQLite DB of `nomic-embed-text` embeddings for all vault notes. Supports `--incremental` to skip unchanged files. |
| `hooks/update-vault-index.sh` | `Stop` | Thin wrapper that calls `build_vault_index.py --incremental` after each session, logging to `~/.claude/vault-index.log`. |
| `hooks/obsidian-bg-agent.sh` | `PostCompact` | After Claude compacts context, runs a headless agent that propagates the session summary to the vault. Opt-in: requires `OBSIDIAN_BG_AGENT_ENABLED=1`. |
| `hooks/validate-ai-first.sh` | `PostToolUse (Write\|Edit)` | Validates every vault write against the AI-first rule: frontmatter, `## For future Claude` preamble, no banned Unicode. Non-blocking — surfaces warnings back to Claude to self-correct. |

### Hook config

- `hooks/obsidian-bg-agent.hook.yaml` — platform-neutral spec for the PostCompact hook
- `hooks/postcompact.hook.example.json` — ready-to-paste JSON for `~/.claude/settings.json`
- `hooks/validate-ai-first.hook.yaml` — platform-neutral spec for the PostToolUse validator

## Setup

Full setup takes about 10 minutes. You need: [Claude Code](https://claude.ai/code) installed, [Obsidian](https://obsidian.md) with an existing vault, macOS (paths below assume macOS; adjust for Linux).

### Step 1 — Install the upstream obsidian-second-brain skill

The slash commands (`/obsidian-save`, `/obsidian-daily`, etc.) come from the upstream skill, not this repo.

```bash
# Clone the upstream skill into Claude's skills directory
git clone https://github.com/eugeniughelbur/obsidian-second-brain \
  ~/.claude/skills/obsidian-second-brain
```

Follow any additional install steps in the [upstream README](https://github.com/eugeniughelbur/obsidian-second-brain#install) (superpowers plugin registration, etc.).

### Step 2 — Clone this repo

Pick a permanent location — the hook commands will reference absolute paths inside it.

```bash
git clone https://github.com/guidodl/obsidian-second-brain \
  ~/obsidian-second-brain-hooks
# or wherever you prefer, e.g. ~/.claude/obsidian-second-brain
```

### Step 3 — Install ollama + embedding model

The vector search hook requires [ollama](https://ollama.com) running locally.

```bash
# macOS
brew install ollama

# Start the server (or add it to your login items)
ollama serve &

# Pull the embedding model (~274 MB, one-time download)
ollama pull nomic-embed-text
```

Verify it's working:
```bash
curl -s http://localhost:11434/api/embeddings \
  -d '{"model":"nomic-embed-text","prompt":"test"}' | python3 -c "import json,sys; print(len(json.load(sys.stdin)['embedding']), 'dims')"
# should print: 768 dims
```

### Step 4 — Copy runtime scripts to `~/.claude/`

These three scripts are called directly by the hooks at runtime:

```bash
REPO=~/obsidian-second-brain-hooks   # adjust to wherever you cloned in Step 2

cp "$REPO/hooks/obsidian-find-hook.py"  ~/.claude/obsidian-find-hook.py
cp "$REPO/hooks/build_vault_index.py"   ~/.claude/build_vault_index.py
cp "$REPO/hooks/update-vault-index.sh"  ~/.claude/update-vault-index.sh
chmod +x "$REPO/hooks/obsidian-bg-agent.sh" \
         "$REPO/hooks/validate-ai-first.sh" \
         ~/.claude/update-vault-index.sh
```

### Step 5 — Build the initial vault index

Run this once to embed all your vault notes into `~/.claude/vault-index.db`:

```bash
OBSIDIAN_VAULT_PATH=/path/to/your/vault python3 ~/.claude/build_vault_index.py
```

This takes a few minutes on a large vault (a 155-note vault takes ~2 min). After the initial build, the Stop hook keeps it updated incrementally.

### Step 6 — Configure `~/.claude/settings.json`

Add the env vars and hook entries. If `settings.json` already exists, merge the `env` and `hooks` blocks — don't replace the whole file.

```json
{
  "env": {
    "OBSIDIAN_VAULT_PATH": "/path/to/your/vault",
    "OBSIDIAN_BG_AGENT_ENABLED": "1"
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/repo/hooks/load_vault_context.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/obsidian-find-hook.py",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/repo/hooks/validate-ai-first.sh"
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/repo/hooks/obsidian-bg-agent.sh",
            "timeout": 10,
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "OBSIDIAN_VAULT_PATH=/path/to/your/vault /opt/homebrew/bin/claude --dangerously-skip-permissions -p 'Read ~/.claude/skills/obsidian-second-brain/obsidian-second-brain.md and run /obsidian-save on this session.' 2>/dev/null || true",
            "timeout": 120,
            "async": true
          },
          {
            "type": "command",
            "command": "OBSIDIAN_VAULT_PATH=/path/to/your/vault bash ~/.claude/update-vault-index.sh",
            "timeout": 300,
            "async": true
          }
        ]
      }
    ]
  }
}
```

Replace every `/path/to/repo` with the absolute path from Step 2, and `/path/to/your/vault` with your Obsidian vault path.

### Step 7 — Initialize your vault

The vault needs a `_CLAUDE.md` operating manual for the SessionStart hook to inject. Start a new Claude Code session in your vault directory and run:

```
/obsidian-init
```

This creates `_CLAUDE.md`, the folder structure, and seed notes. You only need to do this once.

### Step 8 — Verify

Open a new Claude Code session. You should see:

1. **SessionStart context injected** — Claude knows your vault structure without being told
2. **System reminder on first message** — a block labelled `Relevant wiki notes (vector search):` appears at the top of Claude's context with matching note snippets
3. **Auto-save on session end** — after you `/exit`, a headless Claude agent runs `/obsidian-save` and the index updates

If the vector search block is missing, check that ollama is running (`ollama list`) and the DB exists (`ls ~/.claude/vault-index.db`).

## Benchmark (2026-06-05, 155 files / 387 chunks)

### Latency

| Hook | Trigger | Avg latency |
|---|---|---|
| `SessionStart` (`load_vault_context.py`) | Once per session | ~67ms |
| `UserPromptSubmit` (`obsidian-find-hook.py`) | Every message | ~124ms |

Both well under the 10s timeout.

### Token footprint

| Hook | Output size | Approx tokens |
|---|---|---|
| SessionStart | 40,582 chars | ~10,145 tokens (once per session) |
| UserPromptSubmit (per message) | ~1,133 chars | ~283 tokens |

Estimated **5,000–15,000 tokens saved per session** vs manual `Read` calls.

### Accuracy: grep vs vector search (same 5 test prompts)

| Prompt | Grep (~60%) | Vector (~95%) |
|---|---|---|
| TLS cert webhook | ✅ hit 1 | ✅ hit 1+3+4+5 all relevant |
| migration push failure | ❌ wrong | ✅ hit 1+2+4 exact |
| slack irq DM support | ❌ wrong | ✅ hit 1+3 correct |
| copilot code review | ✅ hit 3 | ✅ hit 1+3 exact, moved to top |
| cancel queued runs | ✅ hit 1 | ✅ hit 1+3+4 all relevant |

**~60% → ~95% top-5 accuracy** after switching from keyword grep to vector search.

## Notes

- `obsidian-find-hook.py`, `build_vault_index.py`, and `update-vault-index.sh` live at `~/.claude/` locally — committed here for backup and portability.
- The vector index (`vault-index.db`) is rebuilt incrementally on every Stop event — only notes changed since the last run are re-embedded.
- If ollama is not running, `obsidian-find-hook.py` falls back to keyword grep automatically.
- The bg-agent (`obsidian-bg-agent.sh`) only activates when both `OBSIDIAN_VAULT_PATH` and `OBSIDIAN_BG_AGENT_ENABLED=1` are set — safe to deploy without the second flag while testing.
