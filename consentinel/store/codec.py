"""Dataclass <-> Firestore document conversion.

Written once, generically, rather than six pairs of hand-rolled converters —
because the frozen contract will gain fields and a hand-rolled codec is where
those quietly get dropped.

Handles: enums (stored as their string value), datetimes (native Firestore
timestamps), Optional, and lists of any of the above.
"""

from __future__ import annotations

import dataclasses
import typing
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, TypeVar, Union, get_args, get_origin

T = TypeVar("T")

_NoneType = type(None)


def _unwrap_optional(tp: Any) -> Any:
    """Optional[X] -> X. Leaves everything else alone."""
    if get_origin(tp) is Union:
        args = [a for a in get_args(tp) if a is not _NoneType]
        if len(args) == 1:
            return args[0]
    return tp


def _to_doc_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        # Firestore stores UTC; a naive datetime is ambiguous, so pin it.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (list, tuple)):
        return [_to_doc_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_doc_value(v) for k, v in value.items()}
    return value


def to_doc(record: Any, *, drop_none: bool = False) -> dict[str, Any]:
    """Serialise a dataclass instance to a Firestore document."""
    if not dataclasses.is_dataclass(record):
        raise TypeError(f"{type(record).__name__} is not a dataclass")
    doc: dict[str, Any] = {}
    for f in dataclasses.fields(record):
        v = _to_doc_value(getattr(record, f.name))
        if v is None and drop_none:
            continue
        doc[f.name] = v
    return doc


def _from_doc_value(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    tp = _unwrap_optional(tp)
    origin = get_origin(tp)

    if origin in (list, tuple):
        (inner,) = get_args(tp) or (Any,)
        seq = [_from_doc_value(inner, v) for v in (value or [])]
        return tuple(seq) if origin is tuple else seq

    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)

    if isinstance(tp, type) and issubclass(tp, datetime):
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        # Firestore may hand back its own timestamp wrapper
        conv = getattr(value, "ToDatetime", None) or getattr(value, "timestamp_pb", None)
        if callable(conv):
            return value.ToDatetime().replace(tzinfo=timezone.utc)
    return value


def from_doc(cls: type[T], doc: dict[str, Any]) -> T:
    """Rebuild a dataclass instance from a Firestore document.

    Unknown keys are ignored and missing keys fall back to field defaults, so a
    schema addition does not break reads of documents written before it.
    """
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in doc:
            continue
        kwargs[f.name] = _from_doc_value(hints.get(f.name, Any), doc[f.name])
    return cls(**kwargs)  # type: ignore[call-arg]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def coerce_dt(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
