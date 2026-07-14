"""Deterministic identity and source metadata for the portable AMF client."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path


CLIENT_METADATA_SCHEMA = "obsidian-amf-client/v1"
CLIENT_NAME = "obsidian_amf"
CLIENT_VERSION = "1.0.0"
SOURCE_FILES = (
    "__init__.py",
    "__main__.py",
    "bridge.py",
    "context_signer.py",
    "credentials.py",
    "metadata.py",
    "projections.py",
)


def client_source_root() -> Path:
    """Return the installed module root without assuming a harness layout."""
    return Path(__file__).resolve().parent


def _source_files(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for relative_path in SOURCE_FILES:
        path = root / relative_path
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            raise ValueError(f"client_source_missing:{relative_path}") from error
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"client_source_unsafe:{relative_path}")
        content = path.read_bytes()
        files.append({
            "path": relative_path,
            "size": len(content),
            "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        })
    return files


def client_metadata(source_root: Path | None = None) -> dict[str, object]:
    """Return location-independent metadata over the exact installable bytes."""
    root = source_root or client_source_root()
    files = _source_files(root)
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema": CLIENT_METADATA_SCHEMA,
        "name": CLIENT_NAME,
        "version": CLIENT_VERSION,
        "python": ">=3.10",
        "entrypoint": "python3 -m obsidian_amf",
        "capabilities": [
            "document-capture",
            "contextual-search",
            "memory-proposals",
            "selected-projections",
        ],
        "modes": ["standalone", "shadow", "active"],
        "scheduledModes": ["shadow"],
        "source": {
            "digest": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
            "files": files,
        },
    }


def client_identity() -> dict[str, str]:
    """Return the compact parity identity included in normal client status."""
    metadata = client_metadata()
    source = metadata["source"]
    assert isinstance(source, dict)
    digest = source["digest"]
    assert isinstance(digest, str)
    return {"name": CLIENT_NAME, "version": CLIENT_VERSION, "sourceDigest": digest}
