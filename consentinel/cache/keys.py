"""How a cache key is built, and why each part is in it.

Two of these parts are load-bearing and both were learned the hard way
somewhere or other:

**`prompt_version`** goes in every key that involves a model. Without it you
tune a prompt, the old answers keep coming back, and you spend an hour debugging
output produced by wording you already deleted.

**`locale`** goes in every key that touches the web. Without it a cached US
result gets served for a Brazilian query, and since territory decides the
verdict, the whole answer is quietly wrong rather than obviously broken.

There is deliberately no `verdict_key`. Verdicts are never cached — they are a
function of a registry that changes, so a consent expiring or being revoked
would silently invalidate a stored answer. Recompute them; the expensive inputs
are already cached.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Union


def sha256_of(data: Union[bytes, str, Path]) -> str:
    if isinstance(data, Path):
        return hashlib.sha256(data.read_bytes()).hexdigest()
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_key(kind: str, content: Union[bytes, str, Path], *, prompt_version: str) -> str:
    """For anything derived from a file or a blob of text we already hold.

    Content-addressed, so it never expires: the same bytes can only ever produce
    the same answer. Re-running a contract or a clip during development is free
    after the first pass, which is where most of the saving actually comes from.
    """
    return f"{kind}:{sha256_of(content)}:{prompt_version}"


def web_key(
    kind: str,
    target: str,
    *,
    locale: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> str:
    """For anything fetched from the open web, which changes underneath us.

    `locale` is required in practice for search and page fetches — see the module
    docstring. It is optional in the signature only because a few web-facing
    lookups genuinely have no locale (an image hash, for instance).
    """
    parts = [kind, hashlib.sha256(target.encode("utf-8")).hexdigest()[:32]]
    if locale:
        parts.append(locale)
    if prompt_version:
        parts.append(prompt_version)
    return ":".join(parts)


def normalise_url(url: str) -> str:
    """Strip the parts of a URL that do not change what you get back.

    Fragments never reach the server, and query parameters in a different order
    are the same request. Without this the same page arrives three times as
    three separate findings.
    """
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    s = urlsplit(url.strip())
    query = urlencode(sorted(parse_qsl(s.query, keep_blank_values=True)))
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path or "/", query, ""))
