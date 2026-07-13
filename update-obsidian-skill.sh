#!/usr/bin/env bash
# Safely update the obsidian-second-brain skill to the latest UPSTREAM release
# tag while preserving local customizations (currently: hooks/load_vault_context.py
# and uv.lock). Keeps the clean fast-forward path intact by never committing the
# local overrides onto main.
#
# Usage:
#   update-obsidian-skill.sh            # update to latest release tag
#   update-obsidian-skill.sh main       # update to origin/main tip (bleeding edge)
set -euo pipefail

SKILL_DIR="$HOME/.claude/skills/obsidian-second-brain"
TARGET="${1:-}"

cd "$SKILL_DIR"

echo "Fetching upstream..."
git fetch origin --tags --quiet

if [ -z "$TARGET" ]; then
  # This repo has two tag schemes (old v1-v4 on early commits, releases on v0.x),
  # so version-sorting tags is misleading. The GitHub "Latest" release is authoritative.
  TARGET="$(gh release view --repo eugeniughelbur/obsidian-second-brain --json tagName -q .tagName 2>/dev/null || true)"
  if [ -z "$TARGET" ]; then
    echo "ERROR: could not determine latest release via gh. Pass a tag or 'main' explicitly." >&2
    exit 1
  fi
  echo "Latest release tag: $TARGET"
elif [ "$TARGET" = "main" ]; then
  TARGET="origin/main"
fi

CURRENT="$(git describe --tags 2>/dev/null || echo unknown)"
echo "Current: $CURRENT  ->  Target: $TARGET"

if git diff --quiet HEAD -- "$TARGET" 2>/dev/null && [ "$CURRENT" = "$TARGET" ]; then
  echo "Already up to date."
  exit 0
fi

# Preserve local overrides.
BACKUP="/tmp/obsidian-skill-backup-$$"
mkdir -p "$BACKUP"
CHANGED="$(git status --porcelain | awk '{print $2}')"
if [ -n "$CHANGED" ]; then
  echo "Backing up local changes to $BACKUP:"
  echo "$CHANGED" | while read -r f; do
    mkdir -p "$BACKUP/$(dirname "$f")"
    cp "$f" "$BACKUP/$f"
    echo "  $f"
  done
  git stash push --quiet -m "auto: local overrides before update to $TARGET" -- $CHANGED
  STASHED=1
else
  STASHED=0
fi

echo "Fast-forwarding to $TARGET..."
if ! git merge --ff-only "$TARGET"; then
  echo "ERROR: fast-forward failed (local commits diverge from upstream)."
  echo "Your changes are safe in the stash and in $BACKUP."
  exit 1
fi

if [ "$STASHED" = "1" ]; then
  echo "Re-applying local overrides..."
  if ! git stash pop; then
    echo ""
    echo "CONFLICT re-applying local overrides. Resolve manually, then:"
    echo "  - keep your version of hooks/load_vault_context.py (merge in any new upstream logic)"
    echo "  - for uv.lock:  git checkout --theirs uv.lock && git add uv.lock"
    echo "Backups of your pre-update files are in: $BACKUP"
    exit 1
  fi
fi

echo ""
echo "Done. Now at: $(git describe --tags). Restart Claude Code to pick up changes."
echo "Backups (safe to delete once verified): $BACKUP"
