# MCP adapter for AMF (`obsidian-amf`)

Governed [Model Context Protocol](https://modelcontextprotocol.io) access to
an Agent Memory Fabric deployment: `obsidian_amf/mcp_server.py` wraps the
`obsidian_amf` bridge as an MCP **stdio** server, so any MCP-native agent can
search the canonical vault-backed memory without a custom client.

**Governance model (read this first):** the adapter enforces nothing itself.
Each harness runs its own adapter process holding **one actor's** credentials
(bearer token file + owner-only context key ring). AMF verifies the bearer,
the purpose-bound signed context token, the policy revision, the scopes, and
the vault ACLs on every call. There is no shared gateway token, and
`amf_propose` only ever queues a proposal for curation — nothing writes
canonical memory directly.

## Tools

| Tool | Arguments | Returns |
| ---- | --------- | ------- |
| `amf_search` | `query` (required), `limit` (1–50, default 5), `purpose` (default `conversation_recall`), `scopes` (default: configured vault scope) | ACL-filtered combined canonical-memory + document results, each citing its vault `path` and a `snippet`, plus per-source counts |
| `amf_status` | — | Bridge health, mode, outbox pending/quarantined counts |
| `amf_propose` | `proposal` (required, complete `amf-memory/v1` object) | Queue receipt with a deterministic content-hash idempotency key (safe retries) |

Valid purposes: `conversation_recall`, `operator_review`, `continuity_resume`,
`memory_curation`.

## Compatibility

The adapter speaks newline-delimited JSON-RPC 2.0 on stdio (MCP stdio
transport). SSE/HTTP transports are not supported.

| Harness | Config location | Status |
| ------- | --------------- | ------ |
| **OpenCode** | `~/.config/opencode/opencode.json` → `mcp` | ✅ tested end-to-end 2026-07-18 |
| **Claude Code** | `claude mcp add` | config provided below |
| **Claude Desktop** | `claude_desktop_config.json` → `mcpServers` | config provided below |
| **Cursor** | `~/.cursor/mcp.json` → `mcpServers` | config provided below |
| **Cline** | `cline_mcp_settings.json` | config provided below |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | config provided below |
| Others (Zed, Goose, Codex CLI, …) | any stdio MCP client | copy the generic block, untested |

Requirements everywhere: Python 3.10+ on the host, this repo checked out, and
a reachable AMF deployment (see
[agent-memory-fabric deploy README](https://github.com/NestDevLab/agent-memory-fabric/blob/main/deploy/README.md)).

## Setup

### 1. Provision the actor (server-side, once per agent)

Each agent needs its own identity in the AMF deployment:

- a row in the auth registry (`tokenSha256`, actor name, `allowedVaults`,
  permissions incl. `memory:search`, `documents:search`, and every
  `purpose:<name>` you intend to use),
- a key version in the server's context key ring,
- the scope(s) registered in the policy file.

Recipe and formats: `agent-memory-fabric/deploy/README.md` ("Local dev
secrets"). Keep the plaintext token and the actor's key-ring file at mode
`0600` — the client refuses unsafe files.

### 2. Decide the environment

| Variable | Example |
| -------- | ------- |
| `PYTHONPATH` | `/path/to/obsidian-second-brain` |
| `OBSIDIAN_VAULT_PATH` | `/path/to/vault` |
| `OBSIDIAN_AMF_VAULT_ID` | `work-wiki` |
| `OBSIDIAN_AMF_URL` | `http://127.0.0.1:8787` |
| `OBSIDIAN_AMF_TOKEN_FILE` | `/private/obsidian/<actor>-token` |
| `OBSIDIAN_AMF_CONTEXT_KEY_RING` | `/private/obsidian/<actor>-context-key-ring.json` |
| `OBSIDIAN_AMF_POLICY_REVISION` | `policy-local-v1` |
| `OBSIDIAN_AMF_ACTOR` | `<actor>` |
| `OBSIDIAN_AMF_SOURCE_INSTANCE` | `<machine>-<agent>` |

### 3. Verify the adapter standalone (30 seconds)

```sh
export PYTHONPATH=/path/to/obsidian-second-brain OBSIDIAN_VAULT_PATH=/path/to/vault \
  OBSIDIAN_AMF_VAULT_ID=work-wiki OBSIDIAN_AMF_URL=http://127.0.0.1:8787 \
  OBSIDIAN_AMF_TOKEN_FILE=/private/obsidian/<actor>-token \
  OBSIDIAN_AMF_CONTEXT_KEY_RING=/private/obsidian/<actor>-context-key-ring.json \
  OBSIDIAN_AMF_POLICY_REVISION=policy-local-v1 OBSIDIAN_AMF_ACTOR=<actor>
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"preflight","version":"0"}}}' \
  | python3 -m obsidian_amf.mcp_server
# → {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", ...
```

### 4. Wire your harness

**OpenCode** — `~/.config/opencode/opencode.json`, inside `"mcp"`:

```json
"amf": {
  "type": "local",
  "command": ["python3", "-m", "obsidian_amf.mcp_server"],
  "enabled": true,
  "environment": { "PYTHONPATH": "...", "OBSIDIAN_VAULT_PATH": "...", "...": "all vars from step 2" }
}
```

Restart OpenCode, then `opencode mcp list` should show `amf` connected
(`opencode mcp debug amf` if not).

**Claude Code**:

```sh
claude mcp add amf python3 -m obsidian_amf.mcp_server \
  --env PYTHONPATH=/path/to/obsidian-second-brain \
  --env OBSIDIAN_VAULT_PATH=/path/to/vault \
  --env OBSIDIAN_AMF_VAULT_ID=work-wiki \
  --env OBSIDIAN_AMF_URL=http://127.0.0.1:8787 \
  --env OBSIDIAN_AMF_TOKEN_FILE=/private/obsidian/<actor>-token \
  --env OBSIDIAN_AMF_CONTEXT_KEY_RING=/private/obsidian/<actor>-context-key-ring.json \
  --env OBSIDIAN_AMF_POLICY_REVISION=policy-local-v1 \
  --env OBSIDIAN_AMF_ACTOR=<actor>
```

**Claude Desktop / Cursor / Cline / Windsurf** — generic `mcpServers` block:

```json
"amf": {
  "command": "python3",
  "args": ["-m", "obsidian_amf.mcp_server"],
  "env": { "PYTHONPATH": "...", "OBSIDIAN_VAULT_PATH": "...", "...": "all vars from step 2" }
}
```

## Examples

Real session (OpenCode, 2026-07-18; agent did its own query refinement):

```
User: Use amf_search to find what I decided about AMF integration mode
⚙ amf_amf_search [query=AMF integration mode decision, purpose=conversation_recall, limit=10]
   → no hits; agent broadens on its own
⚙ amf_amf_search [query=integration mode, ...]
Agent: Found it. You made this decision twice — originally on 2026-07-17,
       then reaffirmed it on 2026-07-18 after briefly reconsidering: …
```

Raw JSON-RPC for a manual call:

```sh
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"amf_search","arguments":{"query":"AMF integration decision","limit":2}}}
```

Result payload (trimmed):

```json
{"items": [{"path": "Decisions/2026-07-18 - Berry to adopt AMF's memory-write proposal seam pattern.md",
            "snippet": "…", "sourceRank": 1, "kind": "document", "vaultId": "work-wiki"}],
 "sources": {"document": 2, "memory": 0}, "nextCursor": null}
```

Prompts that exercise the governed write path:

- *"Use amf_propose to propose a memory that <fact>."* → lands in the
  curation queue only; never a direct canonical write.
- *"Run amf_status."* → health + pending deliveries for this actor.

## Troubleshooting

| Symptom | Cause / fix |
| ------- | ----------- |
| `obsidian-amf mcp: vault_path_required` | `OBSIDIAN_VAULT_PATH` unset or not a directory |
| `context_key_ring_unsafe` | Key-ring (or token) file not `0600`, or is a symlink — the client refuses unsafe files |
| `amf_http_403` on search | Actor missing `purpose:<name>` permission, or the requested scope is not registered in the AMF policy (`scope_forbidden`) |
| `amf_http_401` | Token doesn't hash to an active registry row |
| Server connects but calls fail | AMF stack down/unreachable at `OBSIDIAN_AMF_URL` |
| Harness can't find the module | `PYTHONPATH` must point at this repo's root |
| MCP over HTTP needed | Not supported; stdio only |

## Security notes

- Secrets stay in `0600` files referenced by path; nothing secret is passed as
  a CLI argument (visible in process listings) or stored by the adapter.
- Context tokens are short-lived and bound to the exact query, actor, vault,
  and policy revision; they are signed locally and never persisted.
- Revoking an agent = deactivate its registry row; its adapter process then
  fails closed with 401s. No other actor is affected.
