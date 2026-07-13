"""Explicit, managed projections from selected plain PAM records into Obsidian."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from pathlib import Path
from typing import Callable

from .bridge import utc_timestamp


MEMORY_ID = re.compile(r"^mem_[A-Za-z0-9_-]{8,128}$")


class ProjectionWriter:
    def __init__(self, vault_path: Path, *, now: Callable[[], str] = utc_timestamp):
        if not vault_path.is_dir():
            raise ValueError("vault_not_found")
        self.vault_path = vault_path.resolve()
        self.now = now
        self.runtime_root = vault_path / ".amf"
        self.records_path = self.runtime_root / "records"
        for path in (self.runtime_root, self.records_path):
            if path.exists() and path.is_symlink():
                raise RuntimeError("projection_path_unsafe")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.vault_path not in self.records_path.resolve().parents:
            raise RuntimeError("projection_path_unsafe")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        self.records_fd = os.open(self.records_path, flags)
        opened = os.fstat(self.records_fd)
        observed = os.stat(self.records_path, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
            os.close(self.records_fd)
            raise RuntimeError("projection_path_unsafe")
        self.connection = sqlite3.connect(self.runtime_root / "projections.sqlite")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS projections (
                 memory_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, record_digest TEXT NOT NULL,
                 relative_path TEXT NOT NULL, projected_at TEXT NOT NULL
               )"""
        )
        self.connection.commit()

    def _validate(self, record: dict) -> tuple[str, int, str]:
        memory_id = str(record.get("id", ""))
        revision = record.get("revision")
        claim = record.get("claim")
        lifecycle = record.get("lifecycle")
        if not MEMORY_ID.fullmatch(memory_id) or not isinstance(revision, int) or revision < 1:
            raise ValueError("memory_record_invalid")
        if not isinstance(lifecycle, dict) or lifecycle.get("status") != "active":
            raise ValueError("memory_not_active")
        if not isinstance(claim, dict) or claim.get("encoding") != "plain" or not isinstance(claim.get("text"), str) or not claim["text"].strip():
            raise ValueError("memory_claim_not_plain")
        return memory_id, revision, claim["text"]

    def project(self, record: dict) -> dict:
        memory_id, revision, text = self._validate(record)
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        existing = self.connection.execute("SELECT * FROM projections WHERE memory_id=?", (memory_id,)).fetchone()
        filename = f"{memory_id}.md"
        try:
            target_stat = os.stat(filename, dir_fd=self.records_fd, follow_symlinks=False)
            if not stat.S_ISREG(target_stat.st_mode):
                raise RuntimeError("projection_target_unsafe")
            target_exists = True
        except FileNotFoundError:
            target_exists = False
        if existing:
            if revision < existing["revision"]:
                raise RuntimeError("projection_revision_stale")
            if revision == existing["revision"]:
                if digest != existing["record_digest"]:
                    raise RuntimeError("projection_revision_conflict")
                if target_exists:
                    return {"memoryId": memory_id, "revision": revision, "path": existing["relative_path"], "duplicate": True}
        scope = record.get("scope", {}).get("id", "") if isinstance(record.get("scope"), dict) else ""
        frontmatter = {
            "amf_managed": True, "amf_memory_id": memory_id, "amf_revision": revision,
            "amf_scope": scope, "amf_visibility": record.get("visibility", ""),
        }
        lines = ["---", *(f"{key}: {json.dumps(value)}" for key, value in frontmatter.items()), "---", "", f"# AMF Memory {memory_id}", "", text.strip(), "", "> Managed AMF projection. Edit the canonical PAM record, not this file.", ""]
        content = "\n".join(lines)
        temporary = f".{memory_id}.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=self.records_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, filename, src_dir_fd=self.records_fd, dst_dir_fd=self.records_fd)
            os.fsync(self.records_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.records_fd)
            except FileNotFoundError:
                pass
        relative_path = (Path(".amf") / "records" / filename).as_posix()
        with self.connection:
            self.connection.execute(
                """INSERT INTO projections(memory_id,revision,record_digest,relative_path,projected_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(memory_id) DO UPDATE SET revision=excluded.revision,record_digest=excluded.record_digest,
                     relative_path=excluded.relative_path,projected_at=excluded.projected_at""",
                (memory_id, revision, digest, relative_path, self.now()),
            )
        return {"memoryId": memory_id, "revision": revision, "path": relative_path, "duplicate": False}

    def unproject(self, memory_id: str) -> dict:
        if not MEMORY_ID.fullmatch(memory_id):
            raise ValueError("memory_id_invalid")
        row = self.connection.execute("SELECT * FROM projections WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            return {"memoryId": memory_id, "removed": False}
        filename = f"{memory_id}.md"
        if row["relative_path"] != (Path(".amf") / "records" / filename).as_posix():
            raise RuntimeError("projection_target_unsafe")
        try:
            target_stat = os.stat(filename, dir_fd=self.records_fd, follow_symlinks=False)
            if not stat.S_ISREG(target_stat.st_mode):
                raise RuntimeError("projection_target_unsafe")
            os.unlink(filename, dir_fd=self.records_fd)
            os.fsync(self.records_fd)
        except FileNotFoundError:
            pass
        with self.connection:
            self.connection.execute("DELETE FROM projections WHERE memory_id=?", (memory_id,))
        return {"memoryId": memory_id, "removed": True}

    def close(self) -> None:
        self.connection.close()
        os.close(self.records_fd)

    def __enter__(self) -> "ProjectionWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
