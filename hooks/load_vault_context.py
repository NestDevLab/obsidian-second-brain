#!/usr/bin/env python3
"""SessionStart hook: inject _CLAUDE.md into context once per session.

Fires on every session start as long as OBSIDIAN_VAULT_PATH is set.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "")
    if not vault:
        return 0

    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        pass

    claude_md = Path(vault) / "_CLAUDE.md"
    if not claude_md.is_file():
        return 0

    content = claude_md.read_text(encoding="utf-8")

    v = Path(vault)
    header = (
        f"**Vault root**: `{vault}`\n"
        f"**Key files** (absolute paths - use these directly, no discovery needed):\n"
        f"  - `{v / '_CLAUDE.md'}` - this operating manual (already loaded)\n"
        f"  - `{v / 'index.md'}` - navigation hub\n"
        f"  - `{v / 'log.md'}` - operation log\n"
        "**Do NOT run `ls`, `Glob`, or `Bash` to discover the vault or its folders.**\n"
        "Use the vault root path above and the folder names from the manual below directly.\n\n"
        "---\n\n"
        "Vault operating manual (_CLAUDE.md, loaded once at session start "
        "by the load_vault_context hook - do not re-read on each command):\n\n"
    )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": header + content,
        }
    }
    json.dump(output, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
