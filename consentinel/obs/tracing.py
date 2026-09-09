"""Tracing, so one sweep reads as one story.

A trace answers a question the audit trail cannot: *why did this take so long,
and what did it call to get there.* One trace per sweep, with a span for the
sweep, each agent, each tool call, and each candidate page.

The audit trail and the trace overlap deliberately but are not the same thing:

    audit    permanent, never sampled, for a lawyer asking why a verdict
    trace    sampled, short-lived, for an engineer asking why it was slow

Each audit row carries the trace id, so you can pivot from one to the other.
That is why `CloudTracer.current_ids()` exists.

Exports to Cloud Trace when a project is configured, and falls back to recording
spans in memory otherwise — so tests and laptops need no credentials and no
network.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class RecordedSpan:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list["RecordedSpan"] = field(default_factory=list)
    error: Optional[str] = None


class MemoryTracer:
    """Records the span tree without leaving the process.

    Useful beyond tests: it lets a local run print the shape of a sweep, which
    is how you notice that triage ran nine times when you expected four.
    """

    def __init__(self) -> None:
        self.roots: list[RecordedSpan] = []
        self._stack: list[RecordedSpan] = []

    @contextlib.contextmanager
    def span(self, name: str, attributes: dict[str, Any]) -> Iterator[dict[str, Any]]:
        s = RecordedSpan(name=name, attributes=dict(attributes or {}))
        (self._stack[-1].children if self._stack else self.roots).append(s)
        self._stack.append(s)
        try:
            yield s.attributes
        except Exception as exc:
            s.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._stack.pop()

    def current_ids(self) -> tuple[Optional[str], Optional[str]]:
        return None, None

    def tree(self, span: Optional[RecordedSpan] = None, depth: int = 0) -> list[str]:
        lines: list[str] = []
        for s in ([span] if span else self.roots):
            mark = "  " * depth
            lines.append(f"{mark}{s.name}" + (f"  [{s.error}]" if s.error else ""))
            for child in s.children:
                lines.extend(self.tree(child, depth + 1))
        return lines


class CloudTracer:
    """Exports to Cloud Trace via OpenTelemetry.

    Set up once per process. Cloud Run already propagates a trace header, so a
    span opened here joins the request's trace rather than starting a new one.
    """

    def __init__(self, *, project: Optional[str] = None, service: str = "consentinel") -> None:
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.service = service
        self._tracer = None

    @property
    def tracer(self):
        if self._tracer is None:
            from opentelemetry import trace
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create({"service.name": self.service})
            )
            provider.add_span_processor(
                BatchSpanProcessor(CloudTraceSpanExporter(project_id=self.project))
            )
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self.service)
        return self._tracer

    @contextlib.contextmanager
    def span(self, name: str, attributes: dict[str, Any]) -> Iterator[dict[str, Any]]:
        attrs = dict(attributes or {})
        with self.tracer.start_as_current_span(name) as sp:
            try:
                yield attrs
            except Exception as exc:
                sp.record_exception(exc)
                raise
            finally:
                # Set on the way out, so attributes the harness fills in during
                # the call (verdict, cache age, retry count) are included.
                for k, v in attrs.items():
                    if v is not None:
                        sp.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))

    def current_ids(self) -> tuple[Optional[str], Optional[str]]:
        """The ids to stamp on an audit row and a log line, so all three line up."""
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if not ctx or not ctx.trace_id:
            return None, None
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


def tracer_for(project: Optional[str] = None):
    """Cloud Trace when there is a project, in-memory otherwise.

    Means a laptop and a test need no credentials, and deploy needs no flag.
    """
    project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project or os.environ.get("CONSENTINEL_TRACE") == "memory":
        return MemoryTracer()
    return CloudTracer(project=project)
