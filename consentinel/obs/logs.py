"""Structured logging, and one rule about what never goes in it.

On Cloud Run, a single line of JSON on stdout becomes a Cloud Logging entry with
its fields indexed — no client library, no credentials, nothing to configure. So
that is what this writes.

**The rule: page content never goes in a log.** Not the text, not the model's
answer about it, not a quote. Two reasons, both real:

  * It is attacker-controlled. Anything we log about a hostile page ends up in a
    system read by people, and log viewers render things.
  * It may contain someone's personal data, and logs are retained and widely
    readable inside a project.

So we log *about* content — its hash, its length, what class of thing it was —
and never the content. `redact()` exists to make that the easy path, and
`safe_fields()` refuses the field names we have decided are never loggable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# Field names that must never carry a value into a log line. If a caller passes
# one, it is replaced with a description of the value rather than the value.
NEVER_LOG = frozenset({
    "text", "page_text", "content", "body", "html", "quote", "evidence_quote",
    "excerpt", "excerpts", "document", "transcript", "prompt", "instruction",
    "answer", "output_text", "draft_notice", "reasoning",
})

# Cloud Logging reads these exact keys and treats them specially.
SEVERITY = {logging.DEBUG: "DEBUG", logging.INFO: "INFO", logging.WARNING: "WARNING",
            logging.ERROR: "ERROR", logging.CRITICAL: "CRITICAL"}


def describe(value: Any) -> str:
    """What we say instead of the thing itself."""
    if value is None:
        return "none"
    if isinstance(value, (bytes, str)):
        raw = value.encode("utf-8") if isinstance(value, str) else value
        return f"<{len(raw)} bytes, sha256:{hashlib.sha256(raw).hexdigest()[:12]}>"
    if isinstance(value, (list, tuple, set)):
        return f"<{len(value)} items>"
    if isinstance(value, dict):
        return f"<{len(value)} keys>"
    return f"<{type(value).__name__}>"


def safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Strip anything we have decided is never loggable, recursively."""
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if k in NEVER_LOG:
            out[k] = describe(v)
        elif isinstance(v, dict):
            out[k] = safe_fields(v)
        else:
            out[k] = v
    return out


class JsonLogger:
    """One JSON object per line, on stdout.

    Carries `trace` when it is given one, which is what makes a log line clickable
    from a trace in the console — the thing that turns two separate tools into one
    story.
    """

    def __init__(self, *, service: str = "consentinel", project: Optional[str] = None,
                 stream=None) -> None:
        self.service = service
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.stream = stream or sys.stdout

    def log(self, severity: str, message: str, *, trace_id: Optional[str] = None,
            span_id: Optional[str] = None, **fields: Any) -> None:
        entry: dict[str, Any] = {
            "severity": severity,
            "message": message,
            "time": datetime.now(timezone.utc).isoformat(),
            "service": self.service,
            **safe_fields(fields),
        }
        if trace_id and self.project:
            entry["logging.googleapis.com/trace"] = f"projects/{self.project}/traces/{trace_id}"
        if span_id:
            entry["logging.googleapis.com/spanId"] = span_id

        self.stream.write(json.dumps(entry, default=str) + "\n")
        self.stream.flush()

    def info(self, message: str, **kw: Any) -> None:
        self.log("INFO", message, **kw)

    def warning(self, message: str, **kw: Any) -> None:
        self.log("WARNING", message, **kw)

    def error(self, message: str, **kw: Any) -> None:
        self.log("ERROR", message, **kw)
