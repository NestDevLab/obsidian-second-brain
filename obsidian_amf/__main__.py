"""Command-line entry point for the Obsidian AMF bridge."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .bridge import BridgeConfig, ObsidianDocumentBridge


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python3 -m obsidian_amf")
    result.add_argument("command", choices=("scan", "drain", "status"))
    result.add_argument("--vault", default=os.environ.get("OBSIDIAN_VAULT_PATH"))
    result.add_argument("--state-db", default=os.environ.get("OBSIDIAN_AMF_STATE_DB", ".amf/bridge-state.sqlite"))
    result.add_argument("--direct-db", default=os.environ.get("OBSIDIAN_AMF_DIRECT_DB", ".amf/documents.sqlite"))
    result.add_argument("--vault-id", default=os.environ.get("OBSIDIAN_AMF_VAULT_ID"))
    result.add_argument("--source-instance", default=os.environ.get("OBSIDIAN_AMF_SOURCE_INSTANCE", "obsidian-local"))
    result.add_argument("--actor", default=os.environ.get("OBSIDIAN_AMF_ACTOR", "person:local-owner"))
    result.add_argument("--mode", choices=("standalone", "shadow", "active"), default=os.environ.get("OBSIDIAN_AMF_MODE", "standalone"))
    result.add_argument("--amf-url", default=os.environ.get("OBSIDIAN_AMF_URL"))
    result.add_argument("--no-drain", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.vault or not args.vault_id:
        raise SystemExit("--vault and --vault-id are required")
    vault = Path(args.vault).expanduser().resolve()
    state_db = Path(args.state_db).expanduser()
    direct_db = Path(args.direct_db).expanduser()
    if not state_db.is_absolute():
        state_db = vault / state_db
    if not direct_db.is_absolute():
        direct_db = vault / direct_db
    config = BridgeConfig(
        vault_path=vault,
        state_db=state_db,
        direct_db=direct_db,
        vault_id=args.vault_id,
        source_instance=args.source_instance,
        actor=args.actor,
        mode=args.mode,
        amf_url=args.amf_url,
        amf_token=os.environ.get("OBSIDIAN_AMF_TOKEN"),
    )
    with ObsidianDocumentBridge(config) as bridge:
        if args.command == "scan":
            result = {"scan": bridge.scan()}
            if not args.no_drain:
                result["delivery"] = bridge.drain()
        elif args.command == "drain":
            result = bridge.drain()
        else:
            result = bridge.status()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
