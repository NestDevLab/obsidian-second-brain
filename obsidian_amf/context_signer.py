"""Request-bound AMF context tokens for the Obsidian client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
KEY_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def base64url(content: bytes) -> str:
    return base64.urlsafe_b64encode(content).rstrip(b"=").decode("ascii")


def iso_timestamp(moment: datetime) -> str:
    normalized = moment.astimezone(timezone.utc)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def private_json(path: Path) -> dict:
    absolute = path.expanduser().absolute()
    parts = absolute.parts
    directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = -1
    try:
        for part in parts[1:-1]:
            opened_directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory
            )
            os.close(directory)
            directory = opened_directory
        descriptor = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        os.close(directory)
        directory = -1
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_mode & 0o077:
            raise ValueError("context_key_ring_unsafe")
        if opened.st_uid not in {0, os.geteuid()}:
            raise ValueError("context_key_ring_unsafe")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            content = handle.read(opened.st_size + 1)
        if len(content) != opened.st_size:
            raise ValueError("context_key_ring_unsafe")
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "context_key_ring_unsafe":
            raise
        raise ValueError("context_key_ring_unsafe") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory >= 0:
            os.close(directory)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("context_key_ring_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("context_key_ring_invalid")
    return value


def decode_key(value: object) -> bytes:
    raw = str(value or "")
    if re.fullmatch(r"[a-fA-F0-9]{64}", raw):
        return bytes.fromhex(raw)
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("context_key_invalid") from error
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != raw:
        raise ValueError("context_key_invalid")
    return decoded


class ContextSigner:
    """Issue short-lived tokens bound to the exact AMF request."""

    def __init__(
        self,
        key_ring_path: Path,
        *,
        actor: str,
        policy_revision: str,
        vault_id: str,
        runtime: str = "obsidian",
        profile: str = "default",
        clock: Callable[[], datetime] | None = None,
        random_bytes: Callable[[int], bytes] = os.urandom,
        ttl_seconds: int = 60,
    ):
        for value, code in ((actor, "actor_invalid"), (vault_id, "vault_id_invalid")):
            if not IDENTIFIER.fullmatch(value):
                raise ValueError(code)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", runtime) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", profile):
            raise ValueError("context_identity_invalid")
        if not policy_revision or len(policy_revision) > 256 or not 1 <= ttl_seconds <= 300:
            raise ValueError("context_policy_invalid")
        ring = private_json(key_ring_path)
        if set(ring) != {"currentKeyVersion", "keys"} or not isinstance(ring["keys"], dict):
            raise ValueError("context_key_ring_invalid")
        version = str(ring["currentKeyVersion"])
        if not KEY_VERSION.fullmatch(version) or version not in ring["keys"]:
            raise ValueError("context_key_ring_invalid")
        if not ring["keys"] or any(not KEY_VERSION.fullmatch(str(item)) for item in ring["keys"]):
            raise ValueError("context_key_ring_invalid")
        for material in ring["keys"].values():
            decode_key(material)
        self.key = decode_key(ring["keys"][version])
        self.key_version = version
        self.actor = actor
        self.policy_revision = policy_revision
        self.vault_id = vault_id
        self.runtime = runtime
        self.profile = profile
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.random_bytes = random_bytes
        self.ttl_seconds = ttl_seconds

    def issue_context_search(self, request: dict) -> str:
        query = str(request.get("query", ""))
        scopes = sorted(set(map(str, request.get("scopes", []))))
        vault_ids = sorted(set(map(str, request.get("vaultIds", []))))
        limit = int(request.get("limit", 20))
        if not query or not scopes or not vault_ids or self.vault_id not in vault_ids or not 1 <= limit <= 100:
            raise ValueError("context_request_invalid")
        if any(not IDENTIFIER.fullmatch(value) for value in scopes + vault_ids):
            raise ValueError("context_request_invalid")
        normalized_request = {
            "operation": "context_search", "query": query, "scopes": scopes,
            "vaultIds": vault_ids, "limit": limit,
        }
        now = self.clock().astimezone(timezone.utc)
        nonce_bytes = self.random_bytes(16)
        if len(nonce_bytes) != 16:
            raise ValueError("context_random_invalid")
        context_digest = hmac.new(self.key, f"vault:{self.vault_id}".encode(), hashlib.sha256).hexdigest()
        tag = f"hmac-sha256:obsidian-v1:{context_digest}"
        payload = {
            "actor": self.actor,
            "runtime": self.runtime,
            "profile": self.profile,
            "conversationKind": "session",
            "contextTags": {"conversation": [tag]},
            "canonicalScopes": scopes,
            "purpose": str(request.get("purpose", "")),
            "policyRevision": self.policy_revision,
            "issuedAt": iso_timestamp(now),
            "expiresAt": iso_timestamp(now + timedelta(seconds=self.ttl_seconds)),
            "nonce": base64url(nonce_bytes),
            "keyVersion": self.key_version,
            "requestDigest": hashlib.sha256(canonical_json(normalized_request).encode()).hexdigest(),
        }
        if payload["purpose"] not in {"operator_review", "continuity_resume", "memory_curation", "conversation_recall"}:
            raise ValueError("context_purpose_invalid")
        encoded = base64url(canonical_json(payload).encode())
        signature = base64url(hmac.new(self.key, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"
