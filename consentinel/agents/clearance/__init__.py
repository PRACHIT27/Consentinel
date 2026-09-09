"""The clearance check — our own footage, before it ships.

Import from the package, not the modules:

    from consentinel.agents.clearance import Declaration, check_asset, to_asset
"""

from consentinel.agents.clearance.agent import (  # noqa: F401
    POLICY,
    ClearanceOutcome,
    Declaration,
    cache_key,
    check_asset,
    content_hash,
    to_asset,
)
from consentinel.agents.clearance.guardrail import (  # noqa: F401
    VALIDATORS,
    disagrees_with_declaration,
)
from consentinel.agents.clearance.instructions import (  # noqa: F401
    ACCEPTED_MIME,
    INSTRUCTION,
    MAX_INLINE_BYTES,
    MODALITIES,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
)

__all__ = [
    "ACCEPTED_MIME",
    "INSTRUCTION",
    "MAX_INLINE_BYTES",
    "MODALITIES",
    "POLICY",
    "PROMPT_VERSION",
    "RESPONSE_SCHEMA",
    "VALIDATORS",
    "ClearanceOutcome",
    "Declaration",
    "cache_key",
    "check_asset",
    "content_hash",
    "disagrees_with_declaration",
    "to_asset",
]
