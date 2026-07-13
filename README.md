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
        injects the top 3 matching note snippets as context.
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
| `hooks/obsidian-find-hook.py` | `UserPromptSubmit` | Embeds each prompt via ollama, runs cosine similarity against the vault index DB, injects up to 3 matching note snippets as context. Aggregates each note's top 2 chunks, boosts notes whose title/path matches query terms, and drops results below `MIN_SCORE` (default `0.60`, set via `OBSIDIAN_FIND_MIN_SCORE`) so off-topic prompts inject nothing. Falls back to grep if index is absent. |
| `hooks/build_vault_index.py` | (one-shot / Stop) | Builds or rebuilds `~/.claude/vault-index.db` — a SQLite DB of `nomic-embed-text` embeddings for all vault notes. Supports `--incremental` to skip unchanged files. |
| `hooks/update-vault-index.sh` | `Stop` | Thin wrapper that calls `build_vault_index.py --incremental` after each session, logging to `~/.claude/vault-index.log`. |
| `hooks/obsidian-bg-agent.sh` | `PostCompact` | After Claude compacts context, runs a headless agent that propagates the session summary to the vault. Opt-in: requires `OBSIDIAN_BG_AGENT_ENABLED=1`. |
| `hooks/validate-ai-first.sh` | `PostToolUse (Write\|Edit)` | Validates every vault write against the AI-first rule: frontmatter, `## For future Claude` preamble, no banned Unicode. Non-blocking — surfaces warnings back to Claude to self-correct. |

### Hook config

- `hooks/obsidian-bg-agent.hook.yaml` — platform-neutral spec for the PostCompact hook
- `hooks/postcompact.hook.example.json` — ready-to-paste JSON for `~/.claude/settings.json`
- `hooks/validate-ai-first.hook.yaml` — platform-neutral spec for the PostToolUse validator

### Scripts

| File | What it does |
|---|---|
| `update-obsidian-skill.sh` | Safely updates the upstream skill in `~/.claude/skills/obsidian-second-brain` to the latest release while preserving local overrides (e.g. a customized `load_vault_context.py`). Backs up local changes, stashes them, fast-forwards to the latest release tag (resolved via `gh release`, since the upstream repo's tag scheme is misleading), then re-applies the overrides. Pass `main` to target the bleeding-edge tip instead of the latest release. Use this instead of the upstream `update.sh`, whose plain `git pull` conflicts on local overrides. |

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

To keep the skill updated later without losing local overrides, run `./update-obsidian-skill.sh` from this repo (see [Scripts](#scripts)) rather than the upstream `update.sh`.

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
brew install --cask ollama

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
OBSIDIAN_VAULT_PATH=<OBSIDIAN_VAULT_PATH> python3 ~/.claude/build_vault_index.py
```

This takes a few minutes on a large vault (a 155-note vault takes ~2 min). After the initial build, the Stop hook keeps it updated incrementally.

### Step 6 — Configure `~/.claude/settings.json`

Add the env vars and hook entries. If `settings.json` already exists, merge the `env` and `hooks` blocks — don't replace the whole file.

```json
{
  "env": {
    "OBSIDIAN_VAULT_PATH": "<PATH_TO_YOUR_VAULT>",
    "OBSIDIAN_BG_AGENT_ENABLED": "1"
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 <PATH_TO_REPO>/hooks/load_vault_context.py"
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
            "command": "bash <PATH_TO_REPO>/hooks/validate-ai-first.sh"
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
            "command": "bash <PATH_TO_REPO>/hooks/obsidian-bg-agent.sh",
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
"command": "OBSIDIAN_VAULT_PATH=<PATH_TO_YOUR_VAULT> <CLAUDE_BINARY> --name 'obsidian-save (bg)' --model <SAVE_MODEL> --permission-mode default --add-dir <PATH_TO_YOUR_VAULT> --allowedTools 'Read' 'Edit' 'Write' 'Glob' 'Grep' 'Task' 'TodoWrite' 'Skill' 'Bash(mkdir *)' -p 'Read ~/.claude/skills/obsidian-second-brain/SKILL.md and run /obsidian-save on this session.' 2>/dev/null || true",
            "timeout": 120,
            "async": true
          },
          {
            "type": "command",
            "command": "OBSIDIAN_VAULT_PATH=<PATH_TO_YOUR_VAULT> bash ~/.claude/update-vault-index.sh",
            "timeout": 300,
            "async": true
          }
        ]
      }
    ]
  }
}
```

Replace every `<PATH_TO_REPO>` with the absolute path from Step 2, `<PATH_TO_YOUR_VAULT>` with your Obsidian vault path, `<CLAUDE_BINARY>` with what you get from `which claude`, and `<SAVE_MODEL>` with the model to run the auto-save agent on.

> **Auto-save model (cost):** the Stop hook spawns a fresh headless agent on **every turn**, so the model it runs on is the single biggest recurring cost of this setup. The `/obsidian-save` task — scan the conversation, search the vault, write notes — is well within reach of a cheap model, so set `<SAVE_MODEL>` to your fastest/cheapest tier (e.g. `haiku`, or a Haiku inference-profile ARN on Bedrock) rather than the default session model. Drop the `--model` flag entirely to inherit your global default — but on an Opus default that makes the per-turn save agent expensive.

> **Named save session:** the `--name 'obsidian-save (bg)'` flag labels the spawned background agent so it is identifiable in session/process listings and logs, distinct from your interactive session. Rename it freely; it is cosmetic.

> **Stop-hook permissions:** the auto-save agent runs with `--permission-mode default` and an explicit `--allowedTools` allowlist (file tools, subagent `Task`, and `Bash(mkdir *)` only) rather than `--dangerously-skip-permissions`. In headless `-p` mode any tool outside the list is denied automatically, so a misfire can't run arbitrary `Bash` or touch files outside the vault. `--add-dir` grants write access to the vault path. If your global `settings.json` sets `defaultMode: bypassPermissions`, the explicit `--permission-mode default` flag is required to re-enable the allowlist for this spawned session.

### Step 7 — Initialize your vault

The vault needs a `_CLAUDE.md` operating manual for the SessionStart hook to inject. Start a new Claude Code session in your vault directory and run:

```
/obsidian-init
```

This creates `_CLAUDE.md`, the folder structure, and seed notes. You only need to do this once.

If the skill doesn't register automatically, run the setup script manually to be able to execute it:

```bash
bash ~/.claude/skills/obsidian-second-brain/scripts/setup.sh "<PATH_TO_YOUR_VAULT>"
```

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
| SessionStart | ~34KB injected | **~2.3k tokens** (once per session, measured) |
| UserPromptSubmit (per message) | ~1,133 chars | ~283 tokens |

Estimated **5,000–15,000 tokens saved per session** vs manual `Read` calls.

**On the SessionStart footprint (corrected 2026-06-08):** an earlier estimate put this at ~10k tokens from raw char count, and assumed the injection landed in the system prompt and was therefore unbilled. Both were wrong. Measured directly from a fresh, zero-message session, the vault injection lands in **Messages** and is billed as regular input tokens — but the real tokenized footprint is only **~2.3k tokens**, not ~10k. The char-to-token estimate was inflated because the injected files are sparse markdown, not dense prose. (A separate ~33k autocompact buffer of summarized history can accumulate in Messages on long sessions, but that is conversation history, not vault injection.)

### Accuracy: grep vs vector search (same 5 test prompts)

| Prompt | Grep (~60%) | Vector (~95%) |
|---|---|---|
| TLS cert webhook | ✅ hit 1 | ✅ hit 1+3+4+5 all relevant |
| migration push failure | ❌ wrong | ✅ hit 1+2+4 exact |
| slack irq DM support | ❌ wrong | ✅ hit 1+3 correct |
| copilot code review | ✅ hit 3 | ✅ hit 1+3 exact, moved to top |
| cancel queued runs | ✅ hit 1 | ✅ hit 1+3+4 all relevant |

**~60% → ~95% top-5 accuracy** after switching from keyword grep to vector search.

## Comparison: vault vector search vs. graphify

[graphify](https://github.com/safishamsi/graphify) is the closest-looking tool in the Claude Code ecosystem, so it's worth being explicit about why this repo is not a competitor to it — they solve different problems.

| Dimension | This repo (vault vector search) | graphify |
|---|---|---|
| **Problem domain** | Personal knowledge retrieval (work notes, meetings, decisions) | Codebase comprehension for AI coding assistants |
| **Input** | Markdown vault (Obsidian notes) | Code, SQL schemas, docs, PDFs, images, videos |
| **Core mechanic** | `nomic-embed-text` embeddings → cosine similarity → top-5 note chunks injected | AST parsing (Tree-sitter) + LLM extraction → graph + Leiden community detection |
| **Query style** | Semantic: "what did I decide about X?" | Graph traversal: "what calls what, what depends on what?" |
| **Time dimension** | Yes — temporal queries ("what were we discussing two weeks ago?") | No — structural snapshot |
| **Live/incremental** | Stop hook re-indexes every session automatically | `--update` re-extracts changed files on demand |
| **Output** | Note chunks injected into context per session | `graph.html`, `GRAPH_REPORT.md`, `graph.json` |

### On token cost

graphify advertises a ~71.5× reduction (BFS subgraph ≈ 2k tokens vs. ~123k naive). That baseline is *dumping a whole codebase into context* — it never applies to a notes vault, where the relevant slice is already small. The honest baseline for this repo is **manual `Read` calls** Claude would otherwise make to discover context (~5k tokens/session, see Benchmark above). The two numbers measure different things against different baselines and are not directly comparable.

### They compose

graphify's `--obsidian --obsidian-dir <vault>` writes code-graph nodes (functions, modules, dependencies) into a vault as wikilinked notes. This repo's vector search then surfaces those code concepts alongside work notes in the same semantic query — code structure and work context unified in one search. Run graphify after major code changes, write the nodes into the vault, and let the `UserPromptSubmit` hook find them contextually.

## On "Stop Calling It Memory"

[*Stop Calling It Memory: The Problem With Markdown-as-Database*](https://limitededitionjonathan.substack.com/p/stop-calling-it-memory-the-problem) argues that treating a folder of markdown as a memory system is a cargo-cult mistake: markdown can't query, can't traverse relationships, doesn't scale, has no schema, and has no concurrent access. The prescription is to put structured memory in a real database (SQLite) and relationships in a graph DB.

The critique is aimed at setups that load raw markdown into context and hope. **This repo already sits on the database side of that line** — the point the article is making. The separation it argues for is the design here: `_CLAUDE.md` is a *config file* loaded once (the article's own distinction), and the actual retrieval runs against a SQLite store of `nomic-embed-text` embeddings (`~/.claude/vault-index.db`), not the markdown itself.

| Article's critique of "markdown as memory" | This repo |
|---|---|
| **No querying** — brute-force context loading | Prompts are embedded and matched by cosine similarity against a SQLite embedding store; top-K note chunks injected, not the vault (`obsidian-find-hook.py`). |
| **Scale ceiling / token waste** | Config (`_CLAUDE.md`) loaded once at ~2.3k tokens; retrieval is top-3 chunks (~283 tokens/prompt), independent of vault size. |
| **No schema** | Frontmatter schema enforced at write time by `validate-ai-first.sh` (`PostToolUse`), not at the DB layer. |
| **No relationship traversal** | **Valid gap.** Links are `[[wikilinks]]`; there is no graph DB. Traversal is grep/subagent-based, not a graph query. Compose with [graphify](#comparison-vault-vector-search-vs-graphify) if you need structural traversal. |
| **No concurrent access** | Partial. SQLite is single-writer; the Stop-hook re-index, bg-agent, and scheduled agents can contend. Fine for one user, not a multi-agent write bus. |

Two honest caveats where the article's warning still bites:

- **The similarity search is a full-table scan.** `obsidian-find-hook.py` loads every embedding row and computes cosine in Python on each prompt — fine at a few thousand notes, but it trades the article's "context-token ceiling" for a query-latency ceiling. A proper ANN index (`sqlite-vec`, FAISS) removes it.
- **No relationship layer.** The graph-DB half of the article's prescription is genuinely unaddressed here; wikilinks are not a queryable graph.

Bottom line: the markdown vault is the human-editable surface and the durable store of record; the SQLite embedding index is the query layer. The article's mistake-to-avoid — *querying the markdown directly* — is exactly what this repo does not do.

## Notes

- `obsidian-find-hook.py`, `build_vault_index.py`, and `update-vault-index.sh` live at `~/.claude/` locally — committed here for backup and portability.
- The vector index (`vault-index.db`) is rebuilt incrementally on every Stop event — only notes changed since the last run are re-embedded.
- If ollama is not running, `obsidian-find-hook.py` falls back to keyword grep automatically.
- The bg-agent (`obsidian-bg-agent.sh`) only activates when both `OBSIDIAN_VAULT_PATH` and `OBSIDIAN_BG_AGENT_ENABLED=1` are set — safe to deploy without the second flag while testing.
