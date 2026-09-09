# What to cache, and with which key

| What | Key | Expiry |
|---|---|---|
| PDF text, contract extraction | `content_key(..., prompt_version=...)` | none — the hash is the key |
| Media inspection, transcripts | `content_key(..., prompt_version=...)` | none |
| Reverse-image results | `content_key` on the image bytes | none |
| Parallel search results | `web_key(..., locale=...)` | `TTL_SEARCH` |
| Page fetches | `web_key(..., locale=...)` | `TTL_PAGE` |
| Page-text extraction | `content_key` on the page text | `TTL_EXTRACTION` |
| "Found nothing" | same as the lookup | `TTL_NEGATIVE` |
| **Verdicts** | **never cached** | — |

Verdicts derive from a registry that changes. A consent expiring or being
revoked would silently invalidate a stored answer, so they are recomputed. The
expensive inputs are already cached, which is what makes that cheap.
