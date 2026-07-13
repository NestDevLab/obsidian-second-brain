"""Portable Obsidian document capture for Agent Memory Fabric."""

from .bridge import BridgeConfig, ObsidianDocumentBridge
from .projections import ProjectionWriter

__all__ = ["BridgeConfig", "ObsidianDocumentBridge", "ProjectionWriter"]
