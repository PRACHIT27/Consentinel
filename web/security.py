"""Who may do something, as opposed to who may look.

The three read-only screens are public on purpose — a judge has to be able to
open the URL. But anything that *acts* spends real money: reading a contract is
a Gemini call, and a sweep is a Gemini call plus a Parallel call per query. On a
public address with no gate, a passing stranger or a crawler can drain the quota
we need for the demo.

So: reads are open, actions need a key. The key travels as `?k=` on the link and
as a hidden field in the form.

This is a shared secret, not a login. It stops accidental and casual use of a
public endpoint, which is the actual risk here. It is not an identity system and
should not be mistaken for one — if this outlived the hackathon the right answer
would be real accounts.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException, status

ENV_VAR = "CONSENTINEL_ACTION_TOKEN"


def required_token() -> Optional[str]:
    token = os.environ.get(ENV_VAR, "").strip()
    return token or None


def actions_are_open() -> bool:
    """With no token configured, actions are unguarded.

    That is the right default for a laptop, where the app is not reachable from
    outside. In the deployed service the token is always set — see the deploy
    step in `infra/`.
    """
    return required_token() is None


def check(supplied: Optional[str]) -> None:
    """Raise 403 unless the supplied key matches. Constant-time compare."""
    expected = required_token()
    if expected is None:
        return
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action needs the team key. Reads are open; actions are not.",
        )

def allows(supplied: Optional[str]) -> bool:
    """The same question as `check`, without the exception.

    The screens need it to decide whether to draw a button. Drawing one that
    403s is worse than drawing none: on a laptop, where no token is configured,
    the upload forms are supposed to be there.
    """
    expected = required_token()
    if expected is None:
        return True
    return bool(supplied) and hmac.compare_digest(supplied, expected)
