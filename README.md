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
        Greps the vault for keywords in the prompt,
        injects the top 5 matching note snippets as context.

Every vault write (Write/Edit tool)
  └── PostToolUse hook → validate-ai-first.sh
        Checks frontmatter, "For future Claude" preamble,
        required fields. Warns Claude to self-correct if missing.

After context compaction
  └── PostCompact hook → obsidian-bg-agent.sh
        Spawns a headless Claude agent that reads the compaction
        summary and propagates decisions/tasks/people to the vault.

End of session
  └── Stop hook → headless claude -p "/obsidian-save"
        Auto-saves everything vault-worthy from the conversation.
```

The skill itself is what Claude follows when executing commands. The hooks are what make it run without being asked.

## What's in here

### Hooks

| File | Trigger | What it does |
|---|---|---|
| `hooks/load_vault_context.py` | `SessionStart` | Reads `_CLAUDE.md` from the vault and injects it into every session as context. Requires `OBSIDIAN_VAULT_PATH` env var. |
| `hooks/obsidian-bg-agent.sh` | `PostCompact` | After Claude compacts context, runs a headless agent that propagates the session summary to the vault. Opt-in: requires `OBSIDIAN_BG_AGENT_ENABLED=1`. |
| `hooks/validate-ai-first.sh` | `PostToolUse (Write\|Edit)` | Validates every vault write against the AI-first rule: frontmatter, `## For future Claude` preamble, no banned Unicode. Non-blocking — surfaces warnings back to Claude to self-correct. |
| `hooks/obsidian-find-hook.py` | `UserPromptSubmit` | Extracts keywords from each prompt, greps the vault, and injects the top 5 matching note snippets as context before Claude responds. |

### Hook config

- `hooks/obsidian-bg-agent.hook.yaml` — platform-neutral spec for the PostCompact hook
- `hooks/postcompact.hook.example.json` — ready-to-paste JSON for `~/.claude/settings.json`
- `hooks/validate-ai-first.hook.yaml` — platform-neutral spec for the PostToolUse validator

## Setup

### 1. Set env vars in `~/.claude/settings.json`

```json
{
  "env": {
    "OBSIDIAN_VAULT_PATH": "/path/to/your/vault",
    "OBSIDIAN_BG_AGENT_ENABLED": "1"
  }
}
```

### 2. Wire hooks in `~/.claude/settings.json`

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/this/repo/hooks/load_vault_context.py"
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
            "command": "python3 /path/to/this/repo/hooks/obsidian-find-hook.py",
            "timeout": 10
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
            "command": "bash /path/to/this/repo/hooks/obsidian-bg-agent.sh",
            "timeout": 10,
            "async": true
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
            "command": "bash /path/to/this/repo/hooks/validate-ai-first.sh"
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
          }
        ]
      }
    ]
  }
}
```

### 3. Make shell hooks executable

```bash
chmod +x hooks/obsidian-bg-agent.sh hooks/validate-ai-first.sh
```

## Notes

- `obsidian-find-hook.py` lives at `~/.claude/obsidian-find-hook.py` locally — committed here for backup and portability.
- The `Stop` hook runs `/obsidian-save` at the end of every session, auto-saving anything vault-worthy from the conversation.
- The bg-agent (`obsidian-bg-agent.sh`) only activates when both `OBSIDIAN_VAULT_PATH` and `OBSIDIAN_BG_AGENT_ENABLED=1` are set — safe to deploy without the second flag while testing.
