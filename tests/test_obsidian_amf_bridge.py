import base64
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from obsidian_amf import BridgeConfig, ContextSigner, ObsidianDocumentBridge, ProjectionWriter


class RecordingProvider:
    def __init__(self, failures=0, search_result=None, search_error=None):
        self.failures = failures
        self.calls = []
        self.search_result = search_result or {"items": [], "nextCursor": None}
        self.search_error = search_error
        self.proposals = []

    def deliver(self, operation, payload):
        self.calls.append((operation, payload))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("offline")

    def search(self, query, limit):
        return self.search_result

    def context_search(self, **_request):
        if self.search_error:
            raise RuntimeError(self.search_error)
        return self.search_result

    def propose(self, proposal, idempotency_key):
        self.proposals.append((proposal, idempotency_key))
        return {"status": "queued", "idempotencyKey": idempotency_key}


class ObsidianDocumentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.ids = iter((f"doc_{number:032x}" for number in range(1, 100)))
        self.tick = 0

    def tearDown(self):
        self.temp.cleanup()

    def now(self):
        self.tick += 1
        return f"2026-07-13T12:00:{self.tick:02d}.000Z"

    def config(self, mode="standalone"):
        return BridgeConfig(
            vault_path=self.vault,
            state_db=self.root / "state.sqlite",
            direct_db=self.root / "documents.sqlite",
            vault_id="vault-test",
            source_instance="obsidian-test",
            actor="person:test-owner",
            mode=mode,
            amf_url="https://amf.invalid" if mode in {"active", "shadow"} else None,
        )

    def bridge(self, mode="standalone", providers=None):
        return ObsidianDocumentBridge(
            self.config(mode), now=self.now, document_id_factory=lambda: next(self.ids), providers=providers
        )

    def outbox_payloads(self, bridge):
        return [json.loads(row[0]) for row in bridge.connection.execute(
            "SELECT payload_json FROM outbox ORDER BY rowid"
        ).fetchall()]

    def test_create_ignores_internal_directories_and_is_idempotent(self):
        (self.vault / "Projects").mkdir()
        (self.vault / "Projects" / "Plan.md").write_text("# Plan\n", encoding="utf-8")
        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian" / "Internal.md").write_text("ignore", encoding="utf-8")
        with self.bridge() as bridge:
            first = bridge.scan()
            self.assertEqual(first, {"generation": 1, "files": 1, "rejected": 0, "created": 1, "updated": 0, "renamed": 0, "deleted": 0})
            self.assertEqual(bridge.drain(), {"attempted": 1, "delivered": 1, "failed": 0, "quarantined": 0, "pending": 0})
            second = bridge.scan()
            self.assertEqual(second["created"], 0)
            self.assertEqual(second["updated"], 0)
            self.assertEqual(bridge.pending_count(), 0)
            status = bridge.status()
            self.assertEqual(status["cursor"]["generation"], 2)
            self.assertTrue(status["healthy"])
        corpus = sqlite3.connect(self.root / "documents.sqlite")
        row = corpus.execute("SELECT path,revision,tombstone,text FROM documents").fetchone()
        corpus.close()
        self.assertEqual(row, ("Projects/Plan.md", 1, 0, "# Plan\n"))
        self.assertEqual(os.stat(self.root / "state.sqlite").st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.root / "documents.sqlite").st_mode & 0o777, 0o600)

    def test_standalone_search_reads_the_direct_corpus(self):
        (self.vault / "Decisions.md").write_text("We selected SQLite for the local backend.", encoding="utf-8")
        with self.bridge() as bridge:
            bridge.scan()
            bridge.drain()
            result = bridge.search(query="SQLite", scopes=[], purpose="operator_review", context_token="")
        self.assertEqual(result["items"][0]["path"], "Decisions.md")
        self.assertIn("SQLite", result["items"][0]["snippet"])

    def test_rename_preserves_identity_and_delete_appends_tombstone(self):
        original = self.vault / "Original.md"
        original.write_text("same bytes", encoding="utf-8")
        with self.bridge() as bridge:
            bridge.scan()
            bridge.drain()
            first = self.outbox_payloads(bridge)[0]
            original.rename(self.vault / "Renamed.md")
            renamed = bridge.scan()
            self.assertEqual(renamed["renamed"], 1)
            bridge.drain()
            second = self.outbox_payloads(bridge)[1]
            self.assertEqual(second["document"]["documentId"], first["document"]["documentId"])
            self.assertEqual(second["document"]["previousPath"], "Original.md")
            self.assertEqual(second["document"]["revision"], 2)
            (self.vault / "Renamed.md").unlink()
            deleted = bridge.scan()
            self.assertEqual(deleted["deleted"], 1)
            bridge.drain()
            third = self.outbox_payloads(bridge)[2]
            self.assertTrue(third["document"]["tombstone"])
            self.assertEqual(third["expectedRevision"], 2)
            self.assertNotIn("text", third)

    def test_content_change_increments_revision(self):
        note = self.vault / "Note.md"
        note.write_text("one", encoding="utf-8")
        with self.bridge() as bridge:
            bridge.scan()
            bridge.drain()
            note.write_text("two", encoding="utf-8")
            result = bridge.scan()
            self.assertEqual(result["updated"], 1)
            payload = self.outbox_payloads(bridge)[1]
            self.assertEqual(payload["document"]["revision"], 2)
            self.assertEqual(payload["expectedRevision"], 1)
            self.assertEqual(payload["text"], "two")

    def test_outbox_retries_without_rescanning(self):
        (self.vault / "Retry.md").write_text("durable", encoding="utf-8")
        provider = RecordingProvider(failures=1)
        with self.bridge(mode="active", providers={"amf": provider}) as bridge:
            bridge.scan()
            first = bridge.drain()
            self.assertEqual(first["failed"], 1)
            self.assertEqual(first["pending"], 1)
            self.assertFalse(bridge.status()["healthy"])
            second = bridge.drain()
            self.assertEqual(second["delivered"], 1)
            self.assertEqual(second["pending"], 0)
            attempts = bridge.connection.execute("SELECT attempts FROM outbox").fetchone()[0]
            self.assertEqual(attempts, 2)
        self.assertEqual(len(provider.calls), 2)

    def test_active_http_provider_uses_amf_route_and_idempotency_header(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def receive(self):
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                received.append((self.command, self.path, dict(self.headers), payload))
                return payload

            def do_PUT(self):
                self.receive()
                self.send_response(201)
                self.end_headers()

            def do_POST(self):
                self.receive()
                data = {"items": [], "nextCursor": None} if self.path == "/v2/context/search" else {"status": "queued"}
                body = json.dumps({"ok": True, "data": data}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            (self.vault / "Http.md").write_text("transport", encoding="utf-8")
            config = BridgeConfig(
                vault_path=self.vault,
                state_db=self.root / "state.sqlite",
                vault_id="vault-test",
                source_instance="obsidian-test",
                actor="person:test-owner",
                mode="active",
                amf_url=f"http://127.0.0.1:{server.server_port}",
                amf_token="test-token",
            )
            with ObsidianDocumentBridge(config, now=self.now, document_id_factory=lambda: next(self.ids)) as bridge:
                bridge.scan()
                result = bridge.drain()
                self.assertEqual(result["delivered"], 1)
                bridge.search(query="memory", scopes=["shared:global"], purpose="operator_review", context_token="context-token")
                bridge.propose({"record": {}, "rationale": "test", "expectedRevision": 0}, "proposal-key")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        method, path, headers, payload = received[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(path, f"/v2/documents/{payload['document']['documentId']}")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertEqual(headers["Idempotency-Key"], payload["idempotencyKey"])
        context_request = received[1]
        self.assertEqual(context_request[1], "/v2/context/search")
        self.assertEqual(context_request[2]["X-Amf-Context-Token"], "context-token")
        proposal_request = received[2]
        self.assertEqual(proposal_request[1], "/v2/memory/proposals")
        self.assertEqual(proposal_request[2]["Idempotency-Key"], "proposal-key")

    def test_context_signer_binds_exact_query_scope_vault_and_policy(self):
        key = bytes(range(32))
        ring = self.root / "context-key-ring.json"
        ring.write_text(json.dumps({
            "currentKeyVersion": "ctx-obsidian-test-v1",
            "keys": {"ctx-obsidian-test-v1": base64.b64encode(key).decode("ascii")},
        }), encoding="utf-8")
        ring.chmod(0o600)
        signer = ContextSigner(
            ring, actor="person:test-owner", policy_revision="policy-test",
            vault_id="vault-test", runtime="obsidian", profile="canary",
            clock=lambda: datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            random_bytes=lambda size: b"n" * size,
        )
        request = {
            "query": "SQLite decision", "scopes": ["domain:notes"],
            "vaultIds": ["vault-test"], "purpose": "operator_review", "limit": 7,
        }
        token = signer.issue_context_search(request)
        encoded, signature = token.split(".")
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(key, encoded.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(signature, expected_signature)
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=="))
        self.assertEqual(payload["actor"], "person:test-owner")
        self.assertEqual(payload["canonicalScopes"], ["domain:notes"])
        self.assertEqual(payload["keyVersion"], "ctx-obsidian-test-v1")
        self.assertEqual(payload["issuedAt"], "2026-07-13T12:00:00.000Z")
        self.assertEqual(payload["expiresAt"], "2026-07-13T12:01:00.000Z")
        canonical_request = json.dumps({
            "operation": "context_search", "query": "SQLite decision",
            "scopes": ["domain:notes"], "vaultIds": ["vault-test"], "limit": 7,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(payload["requestDigest"], hashlib.sha256(canonical_request.encode()).hexdigest())

    def test_active_search_issues_a_fresh_request_bound_token(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                received.append((self.path, dict(self.headers), payload))
                body = json.dumps({"ok": True, "data": {"items": [], "nextCursor": None}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        key = b"k" * 32
        ring = self.root / "context-key-ring.json"
        ring.write_text(json.dumps({
            "currentKeyVersion": "ctx-obsidian-test-v1",
            "keys": {"ctx-obsidian-test-v1": base64.b64encode(key).decode("ascii")},
        }), encoding="utf-8")
        ring.chmod(0o600)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = BridgeConfig(
                vault_path=self.vault, state_db=self.root / "state.sqlite",
                vault_id="vault-test", source_instance="obsidian-test", actor="person:test-owner",
                mode="active", amf_url=f"http://127.0.0.1:{server.server_port}", amf_token="test-token",
                context_key_ring=ring, policy_revision="policy-test", context_profile="canary",
            )
            with ObsidianDocumentBridge(config) as bridge:
                bridge.search(
                    query="fresh query", scopes=["domain:notes"], purpose="operator_review",
                    context_token="", limit=5,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        path, headers, payload = received[0]
        self.assertEqual(path, "/v2/context/search")
        self.assertEqual(payload["query"], "fresh query")
        token = headers["X-Amf-Context-Token"]
        decoded = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
        self.assertEqual(decoded["purpose"], "operator_review")
        self.assertEqual(decoded["canonicalScopes"], ["domain:notes"])

    def test_context_signer_rejects_permissive_or_symlinked_key_material(self):
        key = base64.b64encode(b"k" * 32).decode("ascii")
        ring = self.root / "ring.json"
        ring.write_text(json.dumps({"currentKeyVersion": "ctx-v1", "keys": {"ctx-v1": key}}), encoding="utf-8")
        ring.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "context_key_ring_unsafe"):
            ContextSigner(ring, actor="person:test", policy_revision="policy-test", vault_id="vault-test")
        ring.chmod(0o600)
        linked_parent = self.root / "linked"
        linked_parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "context_key_ring_unsafe"):
            ContextSigner(linked_parent / "ring.json", actor="person:test", policy_revision="policy-test", vault_id="vault-test")

    def test_shadow_delivers_independently_to_both_providers(self):
        (self.vault / "Shadow.md").write_text("compare", encoding="utf-8")
        direct = RecordingProvider()
        amf = RecordingProvider(failures=1)
        with self.bridge(mode="shadow", providers={"direct": direct, "amf": amf}) as bridge:
            bridge.scan()
            result = bridge.drain()
            self.assertEqual(result, {"attempted": 2, "delivered": 1, "failed": 1, "quarantined": 0, "pending": 1})
            destinations = bridge.connection.execute(
                "SELECT destination,status FROM outbox ORDER BY rowid"
            ).fetchall()
            self.assertEqual([tuple(row) for row in destinations], [("direct", "delivered"), ("amf", "pending")])

    def test_shadow_search_keeps_direct_authoritative_and_compares_amf(self):
        direct = RecordingProvider(search_result={"items": [{"id": "doc_direct"}]})
        amf = RecordingProvider(search_result={"items": [{"id": "mem_amf"}]})
        with self.bridge(mode="shadow", providers={"direct": direct, "amf": amf}) as bridge:
            result = bridge.search(query="decision", scopes=["shared:global"], purpose="operator_review", context_token="signed")
        self.assertEqual(result["authoritative"]["items"][0]["id"], "doc_direct")
        self.assertEqual(result["diagnostic"]["items"][0]["id"], "mem_amf")
        self.assertFalse(result["degraded"])
        self.assertEqual(result["comparison"], {"directIds": ["doc_direct"], "amfIds": ["mem_amf"]})

    def test_shadow_search_survives_amf_outage(self):
        direct = RecordingProvider(search_result={"items": [{"id": "doc_direct"}]})
        amf = RecordingProvider(search_error="amf_unavailable")
        with self.bridge(mode="shadow", providers={"direct": direct, "amf": amf}) as bridge:
            result = bridge.search(query="decision", scopes=["shared:global"], purpose="operator_review", context_token="signed")
        self.assertEqual(result["authoritative"]["items"][0]["id"], "doc_direct")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["diagnostic"]["error"], "amf_unavailable")

    def test_proposals_are_explicit_and_require_an_amf_provider(self):
        proposal = {"record": {"id": "mem_selected"}, "rationale": "selected by operator", "expectedRevision": 0}
        amf = RecordingProvider()
        with self.bridge(mode="active", providers={"amf": amf}) as bridge:
            result = bridge.propose(proposal, "proposal-key")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(amf.proposals, [(proposal, "proposal-key")])
        with self.bridge(mode="standalone", providers={"direct": RecordingProvider()}) as bridge:
            with self.assertRaisesRegex(RuntimeError, "amf_required"):
                bridge.propose(proposal, "proposal-key")

    def test_invalid_utf8_is_visible_as_failed_extraction(self):
        (self.vault / "Binary.md").write_bytes(b"valid\xffinvalid")
        provider = RecordingProvider()
        with self.bridge(mode="active", providers={"amf": provider}) as bridge:
            bridge.scan()
            bridge.drain()
        payload = provider.calls[0][1]
        self.assertIsNone(payload["text"])
        self.assertEqual(payload["document"]["extraction"]["status"], "failed")
        self.assertEqual(payload["document"]["extraction"]["errorCode"], "invalid_utf8")

    def test_symlink_swap_is_rejected_without_tombstoning_the_tracked_note(self):
        note = self.vault / "Tracked.md"
        note.write_text("inside", encoding="utf-8")
        outside = self.root / "outside.md"
        outside.write_text("private outside content", encoding="utf-8")
        with self.bridge() as bridge:
            bridge.scan()
            bridge.drain()
            note.unlink()
            note.symlink_to(outside)
            result = bridge.scan()
            self.assertEqual(result["rejected"], 1)
            self.assertEqual(result["deleted"], 0)
            self.assertFalse(bridge.status()["healthy"])
            self.assertEqual(bridge.connection.execute(
                "SELECT tombstone FROM source_documents WHERE path='Tracked.md'"
            ).fetchone()[0], 0)

    def test_oversized_markdown_is_inventoried_without_sending_its_text(self):
        (self.vault / "Large.md").write_bytes(b"x" * (16 * 1024 * 1024 + 1))
        provider = RecordingProvider()
        with self.bridge(mode="active", providers={"amf": provider}) as bridge:
            result = bridge.scan()
            bridge.drain()
        self.assertEqual(result["rejected"], 0)
        payload = provider.calls[0][1]
        self.assertIsNone(payload["text"])
        self.assertEqual(payload["document"]["extraction"]["errorCode"], "content_too_large")

    def test_tampered_outbox_payload_is_quarantined_before_delivery(self):
        (self.vault / "Queued.md").write_text("safe", encoding="utf-8")
        provider = RecordingProvider()
        with self.bridge(mode="active", providers={"amf": provider}) as bridge:
            bridge.scan()
            with bridge.connection:
                bridge.connection.execute("UPDATE outbox SET payload_json='{}'")
            result = bridge.drain()
            self.assertEqual(result["quarantined"], 1)
            self.assertEqual(result["pending"], 0)
            self.assertFalse(bridge.status()["healthy"])
        self.assertEqual(provider.calls, [])

    def test_rename_requires_identity_and_digest_evidence(self):
        original = self.vault / "A.md"
        original.write_text("shared", encoding="utf-8")
        with self.bridge() as bridge:
            bridge.scan()
            bridge.drain()
            replacement = self.vault / "B.md"
            replacement.write_text("shared", encoding="utf-8")
            original.unlink()
            result = bridge.scan()
            self.assertEqual(result["renamed"], 0)
            self.assertEqual(result["created"], 1)
            self.assertEqual(result["deleted"], 1)

    def test_recreated_path_does_not_inherit_tombstoned_identity_without_evidence(self):
        note = self.vault / "Reused.md"
        note.write_text("old", encoding="utf-8")
        with self.bridge() as bridge:
            bridge.scan()
            bridge.drain()
            first_id = self.outbox_payloads(bridge)[0]["document"]["documentId"]
            note.unlink()
            bridge.scan()
            bridge.drain()
            note.write_text("new", encoding="utf-8")
            result = bridge.scan()
            self.assertEqual(result["created"], 1)
            latest = self.outbox_payloads(bridge)[-1]
            self.assertNotEqual(latest["document"]["documentId"], first_id)
            self.assertEqual(latest["document"]["revision"], 1)

    def test_selected_plain_memory_projection_is_managed_revisioned_and_reversible(self):
        record = {
            "schema": "amf-memory/v1", "id": "mem_selected123", "revision": 1,
            "scope": {"type": "shared", "id": "shared:global"}, "visibility": "shared",
            "claim": {"encoding": "plain", "text": "Use one active database provider."},
            "lifecycle": {"status": "active"},
        }
        with ProjectionWriter(self.vault, now=self.now) as writer:
            created = writer.project(record)
            duplicate = writer.project(record)
            self.assertFalse(created["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            target = self.vault / created["path"]
            self.assertTrue(target.is_file())
            self.assertIn("Use one active database provider.", target.read_text(encoding="utf-8"))
            updated_record = {**record, "revision": 2, "claim": {"encoding": "plain", "text": "Use one swappable database provider."}}
            updated = writer.project(updated_record)
            self.assertEqual(updated["revision"], 2)
            with self.assertRaisesRegex(RuntimeError, "projection_revision_stale"):
                writer.project(record)
            removed = writer.unproject(record["id"])
            self.assertTrue(removed["removed"])
            self.assertFalse(target.exists())

    def test_projection_rejects_sealed_or_inactive_records(self):
        base = {"id": "mem_selected123", "revision": 1, "scope": {"id": "shared:global"}, "visibility": "shared"}
        with ProjectionWriter(self.vault) as writer:
            with self.assertRaisesRegex(ValueError, "memory_claim_not_plain"):
                writer.project({**base, "claim": {"encoding": "sealed", "ciphertext": "opaque"}, "lifecycle": {"status": "active"}})
            with self.assertRaisesRegex(ValueError, "memory_not_active"):
                writer.project({**base, "claim": {"encoding": "plain", "text": "obsolete"}, "lifecycle": {"status": "revoked"}})

    def test_projection_refuses_a_symlink_target(self):
        record = {
            "id": "mem_selected123", "revision": 1, "scope": {"id": "shared:global"}, "visibility": "shared",
            "claim": {"encoding": "plain", "text": "safe content"}, "lifecycle": {"status": "active"},
        }
        outside = self.root / "outside.md"
        outside.write_text("preserve", encoding="utf-8")
        with ProjectionWriter(self.vault) as writer:
            (self.vault / ".amf" / "records" / "mem_selected123.md").symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, "projection_target_unsafe"):
                writer.project(record)
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
