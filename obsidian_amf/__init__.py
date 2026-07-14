"""Portable Obsidian document capture for Agent Memory Fabric."""

from .bridge import BridgeConfig, ObsidianDocumentBridge
from .context_signer import ContextSigner
from .credentials import load_amf_token
from .metadata import CLIENT_VERSION, client_identity, client_metadata, client_source_root
from .projections import ProjectionWriter

__version__ = CLIENT_VERSION

__all__ = [
    "BridgeConfig",
    "CLIENT_VERSION",
    "ContextSigner",
    "ObsidianDocumentBridge",
    "ProjectionWriter",
    "client_identity",
    "client_metadata",
    "client_source_root",
    "load_amf_token",
]
