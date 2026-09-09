"""The cache. Two regimes — see `store.py` for why keeping them apart matters."""

from consentinel.cache.keys import (  # noqa: F401
    content_key,
    normalise_url,
    sha256_of,
    web_key,
)
from consentinel.cache.store import (  # noqa: F401
    INLINE_LIMIT_BYTES,
    FirestoreCache,
)

# Suggested lifetimes. Search results move slowly, pages faster; extracted text
# is keyed by content so it only needs a long backstop.
TTL_SEARCH = 12 * 3600
TTL_PAGE = 3 * 3600
TTL_NEGATIVE = 3600          # "found nothing" — short, so a real result is not hidden
TTL_EXTRACTION = 7 * 86400
