"""Revisioned Markdown capture with a durable outbox and swappable providers."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable


MODES = {"standalone", "shadow", "active"}
MAX_MARKDOWN_BYTES = 16 * 1024 * 1024
EXCLUDED_PARTS = {
    ".git", ".obsidian", ".trash", ".cache", ".amf", "__pycache__",
    "trash", "cache", "caches",
}


def utc_timestamp(value: float | None = None) -> str:
    moment = datetime.fromtimestamp(value, timezone.utc) if value is not None else datetime.now(timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


@dataclass(frozen=True)
class BridgeConfig:
    vault_path: Path
    state_db: Path
    vault_id: str
    source_instance: str
    actor: str
    mode: str = "standalone"
    direct_db: Path | None = None
    amf_url: str | None = None
    amf_token: str | None = None
    timeout_seconds: float = 10.0

    def validate(self) -> "BridgeConfig":
        if self.mode not in MODES:
            raise ValueError("mode_invalid")
        if not self.vault_path.is_dir():
            raise ValueError("vault_not_found")
        for value, code in ((self.vault_id, "vault_id_invalid"), (self.source_instance, "source_instance_invalid"), (self.actor, "actor_invalid")):
            if not value or len(value) > 192 or not all(char.isalnum() or char in ":._-" for char in value):
                raise ValueError(code)
        if self.mode in {"standalone", "shadow"} and self.direct_db is None:
            raise ValueError("direct_db_required")
        if self.mode in {"active", "shadow"} and not self.amf_url:
            raise ValueError("amf_url_required")
        return self


class DirectSqliteProvider:
    """Simple standalone corpus using the same immutable revision semantics."""

    destination = "direct"

    def __init__(self, path: Path):
        if path.parent.exists() and path.parent.is_symlink():
            raise RuntimeError("state_path_unsafe")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS document_revisions (
              idempotency_key TEXT PRIMARY KEY,
              document_id TEXT NOT NULL,
              vault_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              path TEXT NOT NULL,
              tombstone INTEGER NOT NULL,
              content_digest TEXT NOT NULL,
              text TEXT,
              payload_json TEXT NOT NULL,
              UNIQUE(document_id, revision)
            );
            CREATE TABLE IF NOT EXISTS documents (
              document_id TEXT PRIMARY KEY,
              vault_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              path TEXT NOT NULL,
              tombstone INTEGER NOT NULL,
              content_digest TEXT NOT NULL,
              text TEXT,
              payload_json TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS documents_live_path
              ON documents(vault_id, path) WHERE tombstone=0;
            """
        )
        self.connection.commit()

    def deliver(self, operation: str, payload: dict) -> None:
        document = payload["document"]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.connection:
            existing = self.connection.execute(
                "SELECT payload_json FROM document_revisions WHERE idempotency_key=?",
                (payload["idempotencyKey"],),
            ).fetchone()
            if existing:
                if existing["payload_json"] != encoded:
                    raise RuntimeError("idempotency_key_conflict")
                return
            latest = self.connection.execute(
                "SELECT revision FROM documents WHERE document_id=?", (document["documentId"],)
            ).fetchone()
            expected = payload["expectedRevision"]
            if (latest is None and expected is not None) or (latest is not None and latest["revision"] != expected):
                raise RuntimeError("revision_conflict")
            if (latest is None and document["revision"] != 1) or (
                latest is not None and document["revision"] != latest["revision"] + 1
            ):
                raise RuntimeError("revision_conflict")
            values = (
                payload["idempotencyKey"], document["documentId"], document["vaultId"],
                document["revision"], document["path"], int(document["tombstone"]),
                document["contentDigest"], payload.get("text"), encoded,
            )
            self.connection.execute(
                "INSERT INTO document_revisions VALUES (?,?,?,?,?,?,?,?,?)", values
            )
            self.connection.execute(
                """INSERT INTO documents(document_id,vault_id,revision,path,tombstone,content_digest,text,payload_json)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(document_id) DO UPDATE SET
                     vault_id=excluded.vault_id,revision=excluded.revision,path=excluded.path,
                     tombstone=excluded.tombstone,content_digest=excluded.content_digest,
                     text=excluded.text,payload_json=excluded.payload_json""",
                values[1:],
            )

    def search(self, query: str, limit: int = 20) -> dict:
        if not query or not 1 <= limit <= 100:
            raise ValueError("search_invalid")
        rows = self.connection.execute(
            """SELECT document_id,revision,vault_id,path,text FROM documents
               WHERE tombstone=0 AND (instr(lower(path),lower(?))>0 OR instr(lower(coalesce(text,'')),lower(?))>0)
               ORDER BY path,document_id LIMIT ?""",
            (query, query, limit),
        ).fetchall()
        items = []
        for rank, row in enumerate(rows, 1):
            text = row["text"] or ""
            match = text.lower().find(query.lower())
            start = max(0, match - 200) if match >= 0 else 0
            snippet = ("…" if start else "") + text[start:start + 600] + ("…" if start + 600 < len(text) else "")
            items.append({
                "kind": "document", "sourceRank": rank, "id": row["document_id"],
                "revision": row["revision"], "vaultId": row["vault_id"], "path": row["path"], "snippet": snippet,
            })
        return {"items": items, "nextCursor": None, "sources": {"memory": 0, "document": len(items)}}

    def close(self) -> None:
        self.connection.close()


class AmfHttpProvider:
    destination = "amf"

    def __init__(self, base_url: str, token: str | None, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), method=method,
            headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {self.token}"} if self.token else {}), **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read() or b"{}")
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"amf_http_{response.status}")
                if body.get("ok") is False:
                    raise RuntimeError(f"amf_{body.get('error', {}).get('code', 'request_failed')}")
                return body.get("data", body)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"amf_http_{error.code}") from error
        except urllib.error.URLError as error:
            raise RuntimeError("amf_unavailable") from error

    def deliver(self, operation: str, payload: dict) -> None:
        document_id = payload["document"]["documentId"]
        method = "DELETE" if operation == "delete" else "PUT"
        self._request(method, f"/v2/documents/{document_id}", payload, {"Idempotency-Key": payload["idempotencyKey"]})

    def context_search(self, *, query: str, scopes: list[str], vault_ids: list[str], purpose: str,
                       context_token: str, limit: int = 20) -> dict:
        payload = {"query": query, "scopes": scopes, "vaultIds": vault_ids, "purpose": purpose, "limit": limit}
        return self._request("POST", "/v2/context/search", payload, {"X-AMF-Context-Token": context_token})

    def propose(self, proposal: dict, idempotency_key: str) -> dict:
        return self._request("POST", "/v2/memory/proposals", proposal, {"Idempotency-Key": idempotency_key})


class ObsidianDocumentBridge:
    def __init__(
        self,
        config: BridgeConfig,
        *,
        now: Callable[[], str] = utc_timestamp,
        document_id_factory: Callable[[], str] | None = None,
        providers: dict[str, object] | None = None,
    ):
        self.config = config.validate()
        self.now = now
        self.document_id_factory = document_id_factory or (lambda: f"doc_{uuid.uuid4().hex}")
        if self.config.state_db.parent.exists() and self.config.state_db.parent.is_symlink():
            raise RuntimeError("state_path_unsafe")
        self.config.state_db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.config.state_db)
        os.chmod(self.config.state_db, 0o600)
        self.connection.row_factory = sqlite3.Row
        self._init_state()
        if providers is None:
            providers = {}
            if config.mode in {"standalone", "shadow"}:
                providers["direct"] = DirectSqliteProvider(config.direct_db)  # type: ignore[arg-type]
            if config.mode in {"active", "shadow"}:
                providers["amf"] = AmfHttpProvider(config.amf_url or "", config.amf_token, config.timeout_seconds)
        self.providers = providers

    def _init_state(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS scan_cursors (
              vault_id TEXT PRIMARY KEY,
              generation INTEGER NOT NULL,
              completed_at TEXT,
              file_count INTEGER NOT NULL DEFAULT 0,
              rejected_file_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS source_documents (
              document_id TEXT PRIMARY KEY,
              vault_id TEXT NOT NULL,
              path TEXT NOT NULL,
              file_identity TEXT NOT NULL,
              content_digest TEXT NOT NULL,
              revision INTEGER NOT NULL,
              source_modified_at TEXT,
              tombstone INTEGER NOT NULL,
              last_seen_generation INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS source_documents_live_path
              ON source_documents(vault_id, path) WHERE tombstone=0;
            CREATE INDEX IF NOT EXISTS source_documents_identity
              ON source_documents(vault_id, file_identity, content_digest);
            CREATE TABLE IF NOT EXISTS outbox (
              event_id TEXT PRIMARY KEY,
              destination TEXT NOT NULL,
              operation TEXT NOT NULL,
              document_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              payload_digest TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              created_at TEXT NOT NULL,
              delivered_at TEXT,
              UNIQUE(destination, document_id, revision)
            );
            """
        )
        cursor_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(scan_cursors)")}
        if "rejected_file_count" not in cursor_columns:
            self.connection.execute("ALTER TABLE scan_cursors ADD COLUMN rejected_file_count INTEGER NOT NULL DEFAULT 0")
        outbox_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(outbox)")}
        if "payload_digest" not in outbox_columns:
            self.connection.execute("ALTER TABLE outbox ADD COLUMN payload_digest TEXT")
        for row in self.connection.execute("SELECT event_id,payload_json FROM outbox WHERE payload_digest IS NULL"):
            self.connection.execute(
                "UPDATE outbox SET payload_digest=? WHERE event_id=?",
                (hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest(), row["event_id"]),
            )
        self.connection.commit()

    def _destinations(self) -> tuple[str, ...]:
        if self.config.mode == "standalone":
            return ("direct",)
        if self.config.mode == "active":
            return ("amf",)
        return ("direct", "amf")

    def _iter_markdown(self) -> Iterable[str]:
        for path in sorted(self.config.vault_path.rglob("*.md")):
            relative = path.relative_to(self.config.vault_path)
            if any(part.startswith(".") or part.lower() in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.name.endswith("~") or path.name.startswith(".#"):
                continue
            yield PurePosixPath(*relative.parts).as_posix()

    def _read_markdown(self, relative: str) -> dict:
        parts = PurePosixPath(relative).parts
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(self.config.vault_path, directory_flags)
        directory_fd = root_fd
        file_fd = None
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                if directory_fd != root_fd:
                    os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError("source_not_regular")
            hasher = hashlib.sha256()
            chunks = []
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                if before.st_size <= MAX_MARKDOWN_BYTES:
                    chunks.append(chunk)
            after = os.fstat(file_fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                raise RuntimeError("source_changed_during_read")
            content = b"".join(chunks) if before.st_size <= MAX_MARKDOWN_BYTES else None
            if content is None:
                text, extraction_error = None, "content_too_large"
            else:
                try:
                    text, extraction_error = content.decode("utf-8"), None
                except UnicodeDecodeError:
                    text, extraction_error = None, "invalid_utf8"
            return {
                "path": relative, "file_identity": f"{before.st_dev}:{before.st_ino}",
                "digest": f"sha256:{hasher.hexdigest()}", "modified_at": utc_timestamp(before.st_mtime),
                "text": text, "extraction_error": extraction_error,
            }
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd != root_fd:
                os.close(directory_fd)
            os.close(root_fd)

    def _document_payload(
        self,
        *,
        row: sqlite3.Row | None,
        document_id: str,
        path: str,
        previous_path: str | None,
        revision: int,
        digest: str,
        modified_at: str | None,
        text: str | None,
        extraction_error: str | None,
        tombstone: bool,
    ) -> dict:
        digest_hex = digest.removeprefix("sha256:")
        document_key = document_id.removeprefix("doc_")
        extraction = {
            "status": "not_requested" if tombstone else ("failed" if extraction_error else "extracted"),
            "extractor": None if tombstone else "markdown-v1",
            "provider": None if tombstone else "local-markdown",
            "textDigest": None if tombstone or extraction_error else sha256_digest((text or "").encode("utf-8")),
            "errorCode": extraction_error,
        }
        document = {
            "schema": "amf.document/v1",
            "documentId": document_id,
            "vaultId": self.config.vault_id,
            "path": path,
            "previousPath": previous_path,
            "revision": revision,
            "contentDigest": digest,
            "mediaType": "text/markdown",
            "sourceModifiedAt": modified_at,
            "tombstone": tombstone,
            "extraction": extraction,
            "provenance": {
                "sourceKind": "obsidian",
                "sourceInstance": self.config.source_instance,
                "observedAt": self.now(),
                "actor": self.config.actor,
            },
        }
        payload = {
            "document": document,
            "expectedRevision": None if row is None else row["revision"],
            "idempotencyKey": f"doc:{self.config.vault_id}:{document_key}:{revision}:{digest_hex}",
        }
        if not tombstone:
            payload["text"] = text
        return payload

    def _enqueue(self, operation: str, payload: dict) -> None:
        document = payload["document"]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        for destination in self._destinations():
            event_id = hashlib.sha256(f"{destination}\0{payload['idempotencyKey']}".encode()).hexdigest()
            self.connection.execute(
                """INSERT OR IGNORE INTO outbox
                   (event_id,destination,operation,document_id,revision,payload_json,payload_digest,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (event_id, destination, operation, document["documentId"], document["revision"], encoded, payload_digest, self.now()),
            )

    def _verified_outbox_payload(self, row: sqlite3.Row) -> dict:
        encoded = row["payload_json"]
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["payload_digest"]:
            raise RuntimeError("outbox_integrity_failed")
        try:
            payload = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("outbox_integrity_failed") from error
        document = payload.get("document") if isinstance(payload, dict) else None
        if not isinstance(document, dict) or payload.get("idempotencyKey") is None:
            raise RuntimeError("outbox_integrity_failed")
        expected_event = hashlib.sha256(f"{row['destination']}\0{payload['idempotencyKey']}".encode()).hexdigest()
        document_id = str(document.get("documentId", ""))
        content_digest = str(document.get("contentDigest", ""))
        expected_key = (
            f"doc:{document.get('vaultId')}:{document_id.removeprefix('doc_')}:"
            f"{document.get('revision')}:{content_digest.removeprefix('sha256:')}"
        )
        if expected_event != row["event_id"] or document.get("documentId") != row["document_id"] \
                or payload["idempotencyKey"] != expected_key or document.get("revision") != row["revision"] \
                or row["destination"] not in self._destinations() \
                or bool(document.get("tombstone")) != (row["operation"] == "delete"):
            raise RuntimeError("outbox_integrity_failed")
        return payload

    def scan(self) -> dict:
        observed = []
        rejected_paths = []
        discovered_paths = set()
        for relative in self._iter_markdown():
            discovered_paths.add(relative)
            try:
                observed.append(self._read_markdown(relative))
            except (OSError, RuntimeError) as error:
                rejected_paths.append((relative, str(error)[:128]))
        observed_paths = {item["path"] for item in observed}
        created = updated = renamed = deleted = 0
        with self.connection:
            cursor = self.connection.execute(
                "SELECT generation FROM scan_cursors WHERE vault_id=?", (self.config.vault_id,)
            ).fetchone()
            generation = (cursor["generation"] if cursor else 0) + 1
            for relative, _error in rejected_paths:
                self.connection.execute(
                    "UPDATE source_documents SET last_seen_generation=? WHERE vault_id=? AND path=? AND tombstone=0",
                    (generation, self.config.vault_id, relative),
                )
            for item in observed:
                row = self.connection.execute(
                    "SELECT * FROM source_documents WHERE vault_id=? AND path=? AND tombstone=0",
                    (self.config.vault_id, item["path"]),
                ).fetchone()
                previous_path = None
                if row is None:
                    matches = self.connection.execute(
                        """SELECT * FROM source_documents
                           WHERE vault_id=? AND file_identity=? AND content_digest=?""",
                        (self.config.vault_id, item["file_identity"], item["digest"]),
                    ).fetchall()
                    matches = [
                        candidate for candidate in matches
                        if candidate["tombstone"] or candidate["path"] not in observed_paths
                    ]
                    if len(matches) == 1:
                        row = matches[0]
                        previous_path = row["path"] if row["path"] != item["path"] else None
                changed = row is None or row["content_digest"] != item["digest"] or row["path"] != item["path"] or row["tombstone"]
                if not changed:
                    self.connection.execute(
                        "UPDATE source_documents SET file_identity=?,last_seen_generation=? WHERE document_id=?",
                        (item["file_identity"], generation, row["document_id"]),
                    )
                    continue
                document_id = row["document_id"] if row else self.document_id_factory()
                revision = (row["revision"] if row else 0) + 1
                payload = self._document_payload(
                    row=row, document_id=document_id, path=item["path"], previous_path=previous_path,
                    revision=revision, digest=item["digest"], modified_at=item["modified_at"],
                    text=item["text"], extraction_error=item["extraction_error"], tombstone=False,
                )
                if row:
                    self.connection.execute(
                        """UPDATE source_documents SET path=?,file_identity=?,content_digest=?,revision=?,
                           source_modified_at=?,tombstone=0,last_seen_generation=? WHERE document_id=?""",
                        (item["path"], item["file_identity"], item["digest"], revision,
                         item["modified_at"], generation, document_id),
                    )
                    if previous_path:
                        renamed += 1
                    else:
                        updated += 1
                else:
                    self.connection.execute(
                        """INSERT INTO source_documents
                           (document_id,vault_id,path,file_identity,content_digest,revision,source_modified_at,tombstone,last_seen_generation)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (document_id, self.config.vault_id, item["path"], item["file_identity"], item["digest"],
                         revision, item["modified_at"], 0, generation),
                    )
                    created += 1
                self._enqueue("upsert", payload)
            missing = self.connection.execute(
                "SELECT * FROM source_documents WHERE vault_id=? AND tombstone=0 AND last_seen_generation<?",
                (self.config.vault_id, generation),
            ).fetchall()
            for row in missing:
                revision = row["revision"] + 1
                payload = self._document_payload(
                    row=row, document_id=row["document_id"], path=row["path"], previous_path=None,
                    revision=revision, digest=row["content_digest"], modified_at=row["source_modified_at"],
                    text=None, extraction_error=None, tombstone=True,
                )
                self.connection.execute(
                    "UPDATE source_documents SET revision=?,tombstone=1,last_seen_generation=? WHERE document_id=?",
                    (revision, generation, row["document_id"]),
                )
                self._enqueue("delete", payload)
                deleted += 1
            self.connection.execute(
                """INSERT INTO scan_cursors(vault_id,generation,completed_at,file_count,rejected_file_count) VALUES (?,?,?,?,?)
                   ON CONFLICT(vault_id) DO UPDATE SET generation=excluded.generation,
                     completed_at=excluded.completed_at,file_count=excluded.file_count,
                     rejected_file_count=excluded.rejected_file_count""",
                (self.config.vault_id, generation, self.now(), len(discovered_paths), len(rejected_paths)),
            )
        return {"generation": generation, "files": len(discovered_paths), "rejected": len(rejected_paths),
                "created": created, "updated": updated, "renamed": renamed, "deleted": deleted}

    def drain(self, limit: int = 100) -> dict:
        delivered = failed = quarantined = 0
        rows = self.connection.execute(
            "SELECT * FROM outbox WHERE status='pending' ORDER BY rowid LIMIT ?", (limit,)
        ).fetchall()
        for row in rows:
            try:
                payload = self._verified_outbox_payload(row)
                provider = self.providers.get(row["destination"])
                if provider is None:
                    raise RuntimeError("provider_unconfigured")
                provider.deliver(row["operation"], payload)
                with self.connection:
                    self.connection.execute(
                        "UPDATE outbox SET status='delivered',attempts=attempts+1,last_error=NULL,delivered_at=? WHERE event_id=?",
                        (self.now(), row["event_id"]),
                    )
                delivered += 1
            except Exception as error:
                if str(error) == "outbox_integrity_failed":
                    with self.connection:
                        self.connection.execute(
                            "UPDATE outbox SET status='quarantined',attempts=attempts+1,last_error=? WHERE event_id=?",
                            ("outbox_integrity_failed", row["event_id"]),
                        )
                    quarantined += 1
                    continue
                with self.connection:
                    self.connection.execute(
                        "UPDATE outbox SET attempts=attempts+1,last_error=? WHERE event_id=?",
                        (str(error)[:256], row["event_id"]),
                    )
                failed += 1
        return {"attempted": len(rows), "delivered": delivered, "failed": failed, "quarantined": quarantined,
                "pending": self.pending_count()}

    def pending_count(self) -> int:
        return self.connection.execute("SELECT count(*) FROM outbox WHERE status='pending'").fetchone()[0]

    def status(self) -> dict:
        cursor = self.connection.execute(
            "SELECT * FROM scan_cursors WHERE vault_id=?", (self.config.vault_id,)
        ).fetchone()
        failed = self.connection.execute(
            "SELECT count(*) FROM outbox WHERE status='pending' AND attempts>0"
        ).fetchone()[0]
        quarantined = self.connection.execute(
            "SELECT count(*) FROM outbox WHERE status='quarantined'"
        ).fetchone()[0]
        rejected = cursor["rejected_file_count"] if cursor else 0
        return {
            "mode": self.config.mode,
            "vaultId": self.config.vault_id,
            "cursor": dict(cursor) if cursor else None,
            "outbox": {"pending": self.pending_count(), "retrying": failed, "quarantined": quarantined},
            "healthy": failed == 0 and quarantined == 0 and rejected == 0,
        }

    def search(self, *, query: str, scopes: list[str], purpose: str, context_token: str, limit: int = 20) -> dict:
        if self.config.mode == "standalone":
            return self.providers["direct"].search(query, limit)
        if self.config.mode == "active":
            return self.providers["amf"].context_search(
                query=query, scopes=scopes, vault_ids=[self.config.vault_id], purpose=purpose,
                context_token=context_token, limit=limit,
            )
        direct = self.providers["direct"].search(query, limit)
        try:
            amf = self.providers["amf"].context_search(
                query=query, scopes=scopes, vault_ids=[self.config.vault_id], purpose=purpose,
                context_token=context_token, limit=limit,
            )
            degraded = False
        except RuntimeError as error:
            amf = {"items": [], "error": str(error)[:256]}
            degraded = True
        return {
            "mode": "shadow", "authoritative": direct, "diagnostic": amf,
            "degraded": degraded,
            "comparison": {
                "directIds": [item["id"] for item in direct.get("items", [])],
                "amfIds": [item["id"] for item in amf.get("items", [])],
            },
        }

    def propose(self, proposal: dict, idempotency_key: str) -> dict:
        provider = self.providers.get("amf")
        if provider is None:
            raise RuntimeError("amf_required")
        return provider.propose(proposal, idempotency_key)

    def close(self) -> None:
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if close:
                close()
        self.connection.close()

    def __enter__(self) -> "ObsidianDocumentBridge":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
