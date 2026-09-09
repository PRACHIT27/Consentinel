"""Reading a contract into a permission slip.

    agent.py         the steps
    guardrail.py     what can reject an answer
    instructions.py  what we ask, and the shape of the reply

Import from the package, not the modules inside it.
"""

from consentinel.agents.consent_ingest.agent import (  # noqa: F401
    POLICY,
    ConsentDraft,
    cache_key,
    extract_consent,
    read_pages,
    to_consent,
)
from consentinel.agents.consent_ingest.guardrail import (  # noqa: F401
    check_citations_are_real,
    check_vocabulary,
    norm,
)
from consentinel.agents.consent_ingest.instructions import (  # noqa: F401
    INSTRUCTION,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    USES,
)
