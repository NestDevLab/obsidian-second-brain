import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from obsidian_amf import BridgeConfig, ObsidianDocumentBridge


class RecordingProvider:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def deliver(self, operation, payload):
        self.calls.append((operation, payload))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("offline")


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
            self.assertEqual(first, {"generation": 1, "files": 1, "created": 1, "updated": 0, "renamed": 0, "deleted": 0})
            self.assertEqual(bridge.drain(), {"attempted": 1, "delivered": 1, "failed": 0, "pending": 0})
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
            def do_PUT(self):
                length = int(self.headers["Content-Length"])
                received.append((self.command, self.path, dict(self.headers), json.loads(self.rfile.read(length))))
                self.send_response(201)
                self.end_headers()

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
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        method, path, headers, payload = received[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(path, f"/v2/documents/{payload['document']['documentId']}")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertEqual(headers["Idempotency-Key"], payload["idempotencyKey"])

    def test_shadow_delivers_independently_to_both_providers(self):
        (self.vault / "Shadow.md").write_text("compare", encoding="utf-8")
        direct = RecordingProvider()
        amf = RecordingProvider(failures=1)
        with self.bridge(mode="shadow", providers={"direct": direct, "amf": amf}) as bridge:
            bridge.scan()
            result = bridge.drain()
            self.assertEqual(result, {"attempted": 2, "delivered": 1, "failed": 1, "pending": 1})
            destinations = bridge.connection.execute(
                "SELECT destination,status FROM outbox ORDER BY rowid"
            ).fetchall()
            self.assertEqual([tuple(row) for row in destinations], [("direct", "delivered"), ("amf", "pending")])

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


if __name__ == "__main__":
    unittest.main()
