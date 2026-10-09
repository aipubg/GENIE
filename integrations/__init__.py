"""GENIE integrations — specialist engines behind one adapter contract (Phase 11B).

GENIE remains the parent system: it owns identity, memory, missions, routing, permissions,
context and agent coordination. Specialist engines provide their best capability underneath.
"""
from .specialists import (  # noqa: F401
    ADAPTERS, ComfyUIAdapter, DecepticonAdapter, HeyGemAdapter, KronosAdapter,
    MiroFishAdapter, N8nAdapter, OmniVoiceAdapter, SpecialistAdapter,
    capabilities, capability_matrix, get_adapter, invoke,
)

__all__ = ["ADAPTERS", "SpecialistAdapter", "N8nAdapter", "ComfyUIAdapter", "HeyGemAdapter",
           "KronosAdapter", "MiroFishAdapter", "OmniVoiceAdapter", "DecepticonAdapter",
           "capabilities", "capability_matrix", "get_adapter", "invoke"]
