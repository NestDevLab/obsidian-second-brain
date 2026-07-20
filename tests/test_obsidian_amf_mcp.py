import io
import json
import unittest
from types import SimpleNamespace

from obsidian_amf.mcp_server import McpServer, serve


class FakeBridge:
    def __init__(self):
        self.config = SimpleNamespace(vault_id="work-wiki")
        self.closed = False

    def search(self, *, query, scopes, purpose, context_token, limit=20):
        return {
            "authoritative": {
                "items": [
                    {"id": "doc_1", "kind": "document", "path": "Note.md",
                     "snippet": f"hit for {query}", "sourceRank": 1, "vaultId": "work-wiki",
                     "extraField": "stripped"}
                ],
                "sources": {"document": 1, "memory": 0},
                "nextCursor": None,
            }
        }

    def status(self):
        return {"healthy": True, "mode": "active", "outbox": {"pending": 0}}

    def propose(self, proposal, idempotency_key):
        return {"queued": True, "idempotencyKey": idempotency_key}

    def close(self):
        self.closed = True


def make_server():
    return McpServer(FakeBridge)


class McpServerTests(unittest.TestCase):
    def test_initialize_returns_protocol_and_server_info(self):
        response = make_server().handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(response["result"]["serverInfo"]["name"], "obsidian-amf")

    def test_tools_list_exposes_search_status_propose(self):
        response = make_server().handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(names, ["amf_search", "amf_status", "amf_propose"])

    def test_search_maps_items_and_strips_extras(self):
        response = make_server().handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "amf_search", "arguments": {"query": "agent memory", "limit": 2}},
        })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["items"][0]["path"], "Note.md")
        self.assertEqual(payload["items"][0]["snippet"], "hit for agent memory")
        self.assertNotIn("extraField", payload["items"][0])
        self.assertEqual(payload["sources"], {"document": 1, "memory": 0})

    def test_search_defaults_scope_to_configured_vault(self):
        bridge = FakeBridge()
        server = McpServer(lambda: bridge)
        seen = {}

        original = bridge.search
        def spy(**kwargs):
            seen.update(kwargs)
            return original(**kwargs)
        bridge.search = spy
        server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "amf_search", "arguments": {"query": "x"}}})
        self.assertEqual(seen["scopes"], ["work-wiki"])
        self.assertEqual(seen["purpose"], "conversation_recall")

    def test_search_rejects_blank_query_and_bad_purpose(self):
        server = make_server()
        for arguments in ({"query": "  "}, {"query": "x", "purpose": "nope"}):
            response = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                      "params": {"name": "amf_search", "arguments": arguments}})
            self.assertTrue(response["result"]["isError"])

    def test_status_delegates_to_bridge(self):
        response = make_server().handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                         "params": {"name": "amf_status", "arguments": {}}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["healthy"])

    def test_propose_idempotency_key_is_deterministic_content_hash(self):
        server = make_server()
        proposal = {"schema": "amf-memory/v1", "claim": {"text": "x"}}
        first = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                               "params": {"name": "amf_propose", "arguments": {"proposal": proposal}}})
        second = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                "params": {"name": "amf_propose", "arguments": {"proposal": proposal}}})
        key1 = json.loads(first["result"]["content"][0]["text"])["idempotencyKey"]
        key2 = json.loads(second["result"]["content"][0]["text"])["idempotencyKey"]
        self.assertEqual(key1, key2)
        self.assertTrue(key1.startswith("mcp-propose:"))

    def test_unknown_tool_and_method_are_errors(self):
        server = make_server()
        tool = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(tool["result"]["isError"])
        method = server.handle({"jsonrpc": "2.0", "id": 10, "method": "resources/list"})
        self.assertEqual(method["error"]["code"], -32601)

    def test_notifications_get_no_response(self):
        self.assertIsNone(make_server().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_serve_loop_handles_parse_error_and_closes_bridge(self):
        bridge = FakeBridge()
        server = McpServer(lambda: bridge)
        stdin = io.StringIO('not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
                            '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"amf_status","arguments":{}}}\n')
        stdout = io.StringIO()
        serve(server, stdin=stdin, stdout=stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1]["result"], {})
        self.assertTrue(bridge.closed)


if __name__ == "__main__":
    unittest.main()
