"""Portable Obsidian document capture for Agent Memory Fabric."""

from .bridge import BridgeConfig, ObsidianDocumentBridge
from .context_signer import ContextSigner
from .projections import ProjectionWriter

__all__ = ["BridgeConfig", "ContextSigner", "ObsidianDocumentBridge", "ProjectionWriter"]
