"""WU-20 — `DEMO_MODE`. The pipeline runs from cache, or it says why not.

FR-8.3. With `DEMO_MODE=true`, **every** external client — Parallel, Gemini,
Web Risk, page fetches — serves from cache and raises on a miss rather than
reaching the network.

This is insurance, and it is the cheapest insurance in the project. The video
shoot cannot die on a rate limit at 1am the night before submission, and a
demo that silently makes a live call is a demo that can fail live.

Two properties matter more than the feature itself:

* **A miss is loud.** It names what was missing and how to fix it, because a
  quiet fallback to the network defeats the whole point.
* **A miss is not permission.** Every client resolves it the way it resolves
  any other failure — `degraded` for a sweep, `ambiguous` for a reading,
  unsafe for a risk check. Hard rule 10 does not get an exception for
  convenience.

Warm the cache with `python tools/warm_demo_cache.py`, which runs the real
pipeline once against the real services.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

ENV_VAR = "DEMO_MODE"
_TRUTHY = ("1", "true", "yes", "on")

log = logging.getLogger("consentinel.demo_mode")


class DemoModeMiss(RuntimeError):
    """`DEMO_MODE=true` and the answer was not cached.

    Classified permanent by `reliability.classify`, so nothing retries it —
    retrying will not put the answer in the cache.
    """


def is_enabled(override: Optional[bool] = None) -> bool:
    """`override` wins, then the environment. Off unless asked for.

    Every client takes the same `demo_mode: Optional[bool]` field: `True` and
    `False` are explicit, `None` means "read the environment at call time".
    Tests always pass an explicit value, so no test outcome depends on your
    `.env`.
    """
    if override is not None:
        return override
    return os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY


def miss(what: str, key: str, *, detail: str = "") -> DemoModeMiss:
    """The error every client raises on a cold cache. One wording, one shape.

    Names the tool, the cache key, and the way out — a message that only says
    "cache miss" sends someone reading logs at 1am to the wrong place.
    """
    message = (f"{ENV_VAR}=true and no cached result for {what} "
               f"(key={key}); refusing to reach the network. "
               f"Warm the cache with `python tools/warm_demo_cache.py` "
               f"or unset {ENV_VAR}.")
    if detail:
        message = f"{message} {detail}"
    return DemoModeMiss(message)


def describe(enabled: Optional[bool] = None) -> str:
    """One line for a startup log or a screen footer."""
    return (f"{ENV_VAR}=true — cache only, no external calls"
            if is_enabled(enabled) else f"{ENV_VAR}=false — live calls allowed")


def warn_once_if_enabled(component: str, enabled: Optional[bool] = None) -> None:
    """Say it out loud at startup. Somebody will forget it is on."""
    if is_enabled(enabled):
        log.warning("%s: %s", component, describe(enabled))
