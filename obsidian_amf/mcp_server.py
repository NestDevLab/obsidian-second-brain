"""Governed MCP stdio adapter over the obsidian_amf bridge.

Exposes AMF context search, status, and memory proposals as MCP tools so
MCP-native agents (Claude Desktop, OpenCode, Cursor, Cline) can use the
fabric without a custom client. Governance stays server-side: this process
runs with exactly one actor's credentials (bearer token file + owner-only
context key ring from the environment), and AMF enforces that actor's policy
revision, scopes, and vault ACLs on every call. There is no shared gateway
token and no adapter-level trust — each harness runs its own adapter process
with its own actor identity.

Protocol: newline-delimited JSON-RPC 2.0 on stdio (MCP stdio transport).
Python 3.10+ stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from obsidian_amf.bridge import BridgeConfig, ObsidianDocumentBridge
from obsidian_amf.credentials import load_amf_token

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "obsidian-amf", "version": "1.0.0"}
PURPOSES = ("conversation_recall", "operator_review", "continuity_resume", "memory_curation")

TOOLS = [
    {
        "name": "amf_search",
        "description": "Combined canonical-memory + vault document search via AMF. "
                       "Results are ACL-filtered for this adapter's actor and cite vault paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                "purpose": {"type": "string", "enum": list(PURPOSES), "default": "conversation_recall"},
                "scopes": {"type": "array", "items": {"type": "string"},
                           "description": "Memory scopes; defaults to the configured vault scope"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "amf_status",
        "description": "Bridge and outbox status for this actor (health, pending deliveries).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "amf_propose",
        "description": "Queue an amf-memory/v1 proposal for curation. Never writes canonical "
                       "memory directly; reviewed proposals are applied by the fabric.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal": {"type": "object", "description": "Complete amf-memory/v1 proposal"},
            },
            "required": ["proposal"],
        },
    },
]


def config_from_env(env: dict[str, str] | None = None) -> BridgeConfig:
    values = env if env is not None else os.environ
    vault = Path(values.get("OBSIDIAN_VAULT_PATH", "")).expanduser()
    if not vault.is_dir():
        raise ValueError("vault_path_required")
    state_db = Path(values.get("OBSIDIAN_AMF_STATE_DB", ".amf/bridge-state.sqlite"))
    direct_db = Path(values.get("OBSIDIAN_AMF_DIRECT_DB", ".amf/documents.sqlite"))
    key_ring = values.get("OBSIDIAN_AMF_CONTEXT_KEY_RING", "")
    return BridgeConfig(
        vault_path=vault,
        state_db=state_db if state_db.is_absolute() else vault / state_db,
        direct_db=direct_db if direct_db.is_absolute() else vault / direct_db,
        vault_id=values.get("OBSIDIAN_AMF_VAULT_ID", ""),
        source_instance=values.get("OBSIDIAN_AMF_SOURCE_INSTANCE", "mcp-adapter"),
        actor=values.get("OBSIDIAN_AMF_ACTOR", ""),
        mode="active",
        amf_url=values.get("OBSIDIAN_AMF_URL"),
        amf_token=load_amf_token(values),
        context_key_ring=Path(key_ring).expanduser().resolve() if key_ring else None,
        policy_revision=values.get("OBSIDIAN_AMF_POLICY_REVISION"),
        context_runtime=values.get("OBSIDIAN_AMF_CONTEXT_RUNTIME", "mcp"),
        context_profile=values.get("OBSIDIAN_AMF_CONTEXT_PROFILE", "adapter"),
    )


class McpServer:
    """JSON-RPC dispatch with an injectable bridge for deterministic tests."""

    def __init__(self, bridge_factory: Callable[[], Any]):
        self._bridge_factory = bridge_factory
        self._bridge: Any = None

    def _get_bridge(self) -> Any:
        if self._bridge is None:
            self._bridge = self._bridge_factory()
        return self._bridge

    def handle(self, message: dict) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(message, -32600, "invalid_request")
        method = message.get("method", "")
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            return self._result(message, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        if method == "ping":
            return self._result(message, {})
        if method == "tools/list":
            return self._result(message, {"tools": TOOLS})
        if method == "tools/call":
            return self._result(message, self._call_tool(message.get("params") or {}))
        return self._error(message, -32601, "method_not_found")

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            if name == "amf_search":
                payload = self._search(arguments)
            elif name == "amf_status":
                payload = self._get_bridge().status()
            elif name == "amf_propose":
                payload = self._propose(arguments)
            else:
                return {"content": [{"type": "text", "text": f"unknown_tool:{name}"}], "isError": True}
        except (RuntimeError, ValueError) as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}]}

    def _search(self, arguments: dict) -> dict:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query_required")
        limit = int(arguments.get("limit", 5))
        purpose = str(arguments.get("purpose", "conversation_recall"))
        if purpose not in PURPOSES:
            raise ValueError("context_purpose_invalid")
        bridge = self._get_bridge()
        scopes = arguments.get("scopes") or [bridge.config.vault_id]
        result = bridge.search(query=query, scopes=[str(s) for s in scopes],
                               purpose=purpose, context_token="", limit=limit)
        authoritative = result.get("authoritative", result)
        return {
            "items": [
                {k: item.get(k) for k in ("path", "snippet", "sourceRank", "kind", "id", "vaultId")}
                for item in authoritative.get("items", [])
            ],
            "sources": authoritative.get("sources", {}),
            "nextCursor": authoritative.get("nextCursor"),
        }

    def _propose(self, arguments: dict) -> dict:
        proposal = arguments.get("proposal")
        if not isinstance(proposal, dict) or not proposal:
            raise ValueError("proposal_required")
        encoded = json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode("utf-8")
        idempotency_key = f"mcp-propose:{hashlib.sha256(encoded).hexdigest()}"
        return self._get_bridge().propose(proposal, idempotency_key)

    @staticmethod
    def _result(message: dict, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}

    @staticmethod
    def _error(message: dict, code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": code, "message": text}}

    def close(self) -> None:
        if self._bridge is not None:
            self._bridge.close()
            self._bridge = None


def serve(server: McpServer, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps(McpServer._error({}, -32700, "parse_error")) + "\n")
                stdout.flush()
                continue
            response = server.handle(message)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()
    finally:
        server.close()


def main() -> int:
    try:
        config = config_from_env()
    except ValueError as error:
        print(f"obsidian-amf mcp: {error}", file=sys.stderr)
        return 2
    serve(McpServer(lambda: ObsidianDocumentBridge(config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
