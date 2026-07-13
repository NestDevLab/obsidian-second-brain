"""Command-line entry point for the Obsidian AMF bridge."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .bridge import BridgeConfig, ObsidianDocumentBridge
from .projections import ProjectionWriter


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python3 -m obsidian_amf")
    result.add_argument("command", choices=("scan", "drain", "status", "search", "propose", "project", "unproject"))
    result.add_argument("--vault", default=os.environ.get("OBSIDIAN_VAULT_PATH"))
    result.add_argument("--state-db", default=os.environ.get("OBSIDIAN_AMF_STATE_DB", ".amf/bridge-state.sqlite"))
    result.add_argument("--direct-db", default=os.environ.get("OBSIDIAN_AMF_DIRECT_DB", ".amf/documents.sqlite"))
    result.add_argument("--vault-id", default=os.environ.get("OBSIDIAN_AMF_VAULT_ID"))
    result.add_argument("--source-instance", default=os.environ.get("OBSIDIAN_AMF_SOURCE_INSTANCE", "obsidian-local"))
    result.add_argument("--actor", default=os.environ.get("OBSIDIAN_AMF_ACTOR", "person:local-owner"))
    result.add_argument("--mode", choices=("standalone", "shadow", "active"), default=os.environ.get("OBSIDIAN_AMF_MODE", "standalone"))
    result.add_argument("--amf-url", default=os.environ.get("OBSIDIAN_AMF_URL"))
    result.add_argument("--context-token", default=os.environ.get("OBSIDIAN_AMF_CONTEXT_TOKEN"))
    result.add_argument("--query")
    result.add_argument("--scope", action="append", dest="scopes", default=[])
    result.add_argument("--purpose", default="operator_review")
    result.add_argument("--limit", type=int, default=20)
    result.add_argument("--input", help="JSON input file, or - for stdin")
    result.add_argument("--idempotency-key")
    result.add_argument("--memory-id")
    result.add_argument("--no-drain", action="store_true")
    return result


def read_json_input(path: str | None) -> dict:
    if not path:
        raise SystemExit("--input is required")
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    args = parser().parse_args()
    if not args.vault:
        raise SystemExit("--vault is required")
    vault = Path(args.vault).expanduser().resolve()
    if args.command in {"project", "unproject"}:
        with ProjectionWriter(vault) as writer:
            if args.command == "project":
                result = writer.project(read_json_input(args.input))
            else:
                if not args.memory_id:
                    raise SystemExit("--memory-id is required")
                result = writer.unproject(args.memory_id)
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if not args.vault_id:
        raise SystemExit("--vault-id is required")
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
        elif args.command == "search":
            if not args.query:
                raise SystemExit("--query is required")
            if args.mode != "standalone" and (not args.scopes or not args.context_token):
                raise SystemExit("--scope and --context-token are required outside standalone mode")
            result = bridge.search(query=args.query, scopes=args.scopes, purpose=args.purpose,
                                   context_token=args.context_token or "", limit=args.limit)
        elif args.command == "propose":
            if not args.idempotency_key:
                raise SystemExit("--idempotency-key is required")
            result = bridge.propose(read_json_input(args.input), args.idempotency_key)
        else:
            result = bridge.status()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
