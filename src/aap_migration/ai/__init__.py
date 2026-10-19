"""Optional AI layer (IBM Granite via an OpenAI-compatible endpoint).

Everything in this package is optional: when ``AI_ENDPOINT`` is unset,
:func:`is_enabled` returns False and callers must behave exactly as before.
Rules decide root-cause codes; the model only explains, summarises and drafts.
"""

from aap_migration.ai.client import AIClient, AIConfig, is_enabled

__all__ = ["AIClient", "AIConfig", "is_enabled"]
