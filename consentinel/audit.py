"""WU-18 — the trail every decision leaves behind.

For a compliance product the trail *is* the value, not bookkeeping. A verdict
nobody can go back and question is worth very little, so this writes one row per
agent run, permanently, whether the run succeeded or not.

Three properties, in order of how easily they get lost:

**Append-only.** The `Store` interface exposes `append_audit` and `list_audit`
and no update or delete. Removing the capability beats remembering not to use
it, and it is why there is no `delete_audit` here to call by accident.

**It records how old its inputs were.** Every entry carries `from_cache` and
`cache_age_s`. A decision must never imply a freshness it does not have —
claiming we checked the web at 3pm when the answer came from a 9am cache is
exactly the kind of quiet dishonesty that makes an audit trail worthless.

**A gap means we did not run.** Rows are written on failure as well as success.
If a sweep found nothing, there is a row saying so; if a sweep never happened,
there is no row. Those two must never look the same, because someone signs off
on the difference.

Usage is one line, on purpose. If it were awkward people would skip it and
every guarantee above would quietly evaporate:

    audit = FirestoreAudit(store)
    deps = HarnessDeps(audit=audit, prompt_version="v1")

or around a tool call:

    with audit.tool("parallel_search", query=q, locale=str(loc)) as call:
        results = parallel_search(...)
        call.record(from_cache=False, result_count=len(results))
"""

from __future__ import annotations

import contextlib
import itertools
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from consentinel.store.base import AuditEvent, Store


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SEQ = itertools.count()


def _new_id() -> str:
    """Sortable, and sortable *correctly*.

    A millisecond timestamp plus a random tail is not enough: three rows written
    in the same millisecond sort by their random part, which is not the order
    they happened in. So the id carries a process-local counter between the
    timestamp and the random tail — the timestamp orders across processes, the
    counter orders within one, and the random tail keeps two processes from
    colliding.
    """
    return f"aud_{int(time.time() * 1_000_000):016d}_{next(_SEQ):06d}_{uuid.uuid4().hex[:6]}"


@dataclass
class ToolCall:
    """One tool invocation, recorded whether it worked or not."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False
    cache_age_s: Optional[float] = None
    duration_s: float = 0.0
    ok: bool = True
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def record(self, *, from_cache: bool = False, cache_age_s: Optional[float] = None, **extra: Any) -> None:
        """Called inside the `tool()` block to note what actually happened."""
        self.from_cache = from_cache
        self.cache_age_s = cache_age_s
        self.extra.update(extra)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "tool": self.tool,
            "args": self.args,
            "from_cache": self.from_cache,
            "cache_age_s": self.cache_age_s,
            "duration_s": round(self.duration_s, 4),
            "ok": self.ok,
        }
        if self.error:
            d["error"] = self.error
        d.update(self.extra)
        return d


class FirestoreAudit:
    """An `AuditPort` that writes through the `Store`.

    Satisfies the harness's port (`append(event: dict)`) and adds `tool()` for
    recording individual tool calls, plus `trail()` for the UI to read a
    decision back.
    """

    def __init__(self, store: Store, *, actor: str = "", subject_type: Optional[str] = None) -> None:
        self.store = store
        self.actor = actor
        self.subject_type = subject_type
        self._pending: list[ToolCall] = []

    # ------------------------------------------------------------- harness port

    def append(self, event: dict[str, Any]) -> None:
        """Write one row. Called by the harness at step 11.

        Any tool calls collected since the last write are folded in, so an
        agent's row carries the tools it used rather than leaving them
        scattered across separate rows nobody joins up.
        """
        calls = [c.as_dict() for c in self._pending]
        self._pending.clear()
        calls.extend(event.get("tool_calls") or [])

        self.store.append_audit(AuditEvent(
            id=_new_id(),
            ts=_parse_ts(event.get("ts")),
            actor=event.get("actor") or self.actor or "unknown",
            subject_type=event.get("subject_type") or self.subject_type,
            subject_id=event.get("subject_id"),
            inputs=_pick(event, "inputs", "ok", "fail_state", "reason",
                         "armor_findings", "injection_suspected", "duration_s"),
            tool_calls=calls,
            output=event.get("output"),
            prompt_version=event.get("prompt_version"),
        ))

    # -------------------------------------------------------------- tool calls

    @contextlib.contextmanager
    def tool(self, name: str, **args: Any) -> Iterator[ToolCall]:
        """Time a tool call and remember it for the next `append`.

        Records on the way out even if the call raised — a tool that failed is
        part of why a decision came out the way it did.
        """
        call = ToolCall(tool=name, args=args)
        started = time.monotonic()
        try:
            yield call
        except Exception as exc:
            call.ok = False
            call.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            call.duration_s = time.monotonic() - started
            self._pending.append(call)

    # ------------------------------------------------------------------ reading

    def trail(self, subject_id: str, subject_type: Optional[str] = None) -> list[AuditEvent]:
        """Every row touching one finding or asset, oldest first.

        This is what the decision-trail view reads: what a verdict was based on,
        which tools ran, and how old each input was.
        """
        rows = self.store.list_audit(subject_type=subject_type or self.subject_type,
                                     subject_id=subject_id)
        return sorted(rows, key=lambda e: e.ts or _utcnow())


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return _utcnow()


def _pick(src: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Keep the keys we care about, drop the ones that are None."""
    base = dict(src.get("inputs") or {}) if "inputs" in keys else {}
    for k in keys:
        if k == "inputs":
            continue
        v = src.get(k)
        if v is not None:
            base[k] = v
    return base
