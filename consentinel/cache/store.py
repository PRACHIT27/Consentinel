"""WU-19 — the cache. Two regimes, and conflating them is the bug.

**Content-addressed, no expiry.** An uploaded file never changes, so its hash
*is* the key and the entry can never go stale. Contract text, media inspection,
transcripts. This is where the real saving is: re-running the same contract
twenty times while building costs one model call.

**TTL, because the web moves.** Search results, page fetches. These expire, and
their keys carry `locale` — serve a cached US result for a Brazilian query once
and the territory logic is quietly wrong rather than obviously broken.

Where things are kept:

    small entries   Firestore `cache` collection, with a native TTL policy on
                    `expires_at` so Firestore does the deleting. No cron job.
    large blobs     Cloud Storage, content-addressed. Firestore documents cap
                    at 1 MiB, and page text and transcripts get close.

The split is at 100 KB. Both stores are shared across Cloud Run containers,
which is the point — an in-process cache is useless when the next request lands
on a different instance. It also means a warm cache survives a redeploy, so
`DEMO_MODE` can record a video without the network.

Never cached: verdicts. They derive from a mutable registry.
"""

from __future__ import annotations

import json
import os
import pickle
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from consentinel.store.base import CacheEntry

# Firestore documents cap at 1 MiB. Anything approaching that goes to Storage,
# well short of the limit so metadata never tips a document over.
INLINE_LIMIT_BYTES = 100 * 1024

COLLECTION = "cache"
BLOB_PREFIX = "cache/"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FirestoreCache:
    """Satisfies both the `Cache` ABC and the harness's `CachePort`.

    `ttl_seconds=None` means content-addressed: stored without an expiry,
    because the key already guarantees the answer cannot change.
    """

    def __init__(
        self,
        *,
        project: Optional[str] = None,
        prefix: str = "",
        bucket: Optional[str] = None,
        client: Any = None,
        storage_client: Any = None,
    ) -> None:
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.prefix = prefix
        self.bucket_name = bucket or os.environ.get(
            "CONSENTINEL_DERIVED_BUCKET", f"{self.project}-derived"
        )
        self._db = client
        self._gcs = storage_client
        self.stats = {"hits": 0, "misses": 0, "expired": 0, "blobs": 0}

    # ------------------------------------------------------------------ clients

    @property
    def db(self):
        if self._db is None:
            from google.cloud import firestore

            self._db = firestore.Client(project=self.project)
        return self._db

    @property
    def gcs(self):
        if self._gcs is None:
            from google.cloud import storage

            self._gcs = storage.Client(project=self.project)
        return self._gcs

    def _col(self):
        return self.db.collection(f"{self.prefix}{COLLECTION}")

    @staticmethod
    def _doc_id(key: str) -> str:
        # Firestore document ids cannot contain "/" and cap at 1500 bytes, and
        # our keys contain both. Hashing sidesteps the whole question.
        import hashlib

        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    # --------------------------------------------------------------------- read

    def get(self, key: str) -> Optional[CacheEntry]:
        snap = self._col().document(self._doc_id(key)).get()
        if not snap.exists:
            self.stats["misses"] += 1
            return None

        doc = snap.to_dict() or {}
        expires_at = doc.get("expires_at")

        # Firestore's TTL sweep is not instant, so an entry can still be here
        # after its time. Treat it as gone rather than serving stale data — the
        # sweep is housekeeping, not correctness.
        if expires_at is not None:
            exp = expires_at if isinstance(expires_at, datetime) else None
            if exp and _utcnow() >= _aware(exp):
                self.stats["expired"] += 1
                return None

        try:
            value = self._load(doc)
        except Exception:
            # A blob that has been lifecycled away, or an unreadable payload.
            # A cache that raises is worse than a cache that misses.
            self.stats["misses"] += 1
            return None

        self.stats["hits"] += 1
        return CacheEntry(value=value, fetched_at=_aware(doc.get("fetched_at") or _utcnow()))

    # -------------------------------------------------------------------- write

    def put(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        now = _utcnow()
        doc: dict[str, Any] = {
            "key": key,
            "fetched_at": now,
            # Only set when there is a TTL. A missing field is what tells
            # Firestore's TTL policy to leave the document alone, which is how
            # content-addressed entries live forever.
            "expires_at": now + timedelta(seconds=ttl_seconds) if ttl_seconds else None,
        }

        payload = _encode(value)
        if len(payload) <= INLINE_LIMIT_BYTES:
            doc["inline"] = payload
        else:
            doc["blob"] = self._write_blob(key, payload)
            self.stats["blobs"] += 1

        self._col().document(self._doc_id(key)).set(doc)

    # --------------------------------------------------------------------- blobs

    def _write_blob(self, key: str, payload: bytes) -> str:
        name = f"{BLOB_PREFIX}{self._doc_id(key)}"
        self.gcs.bucket(self.bucket_name).blob(name).upload_from_string(
            payload, content_type="application/octet-stream"
        )
        return name

    def _load(self, doc: dict[str, Any]) -> Any:
        if "inline" in doc and doc["inline"] is not None:
            return _decode(doc["inline"])
        name = doc.get("blob")
        if not name:
            raise KeyError("cache document has neither an inline payload nor a blob")
        return _decode(self.gcs.bucket(self.bucket_name).blob(name).download_as_bytes())

    # ----------------------------------------------------------------- teardown

    def purge(self) -> int:
        """Delete every entry under this prefix. Refuses without one.

        Same guard as the store: an unprefixed purge would empty the real cache
        mid-demo, which is the one thing this method must not make easy.
        """
        if not self.prefix:
            raise RuntimeError(
                "purge() needs a collection prefix. Without one this would empty "
                "the live cache."
            )
        n = 0
        for snap in self._col().stream():
            snap.reference.delete()
            n += 1
        return n


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _encode(value: Any) -> bytes:
    """JSON when it can, pickle when it must.

    JSON keeps entries readable in the Firestore console, which matters when you
    are trying to work out why a cached answer looks wrong. Dataclasses and
    other objects fall back to pickle.
    """
    try:
        return b"j" + json.dumps(value).encode("utf-8")
    except (TypeError, ValueError):
        return b"p" + pickle.dumps(value)


def _decode(payload: bytes) -> Any:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    tag, body = payload[:1], payload[1:]
    if tag == b"j":
        return json.loads(body.decode("utf-8"))
    if tag == b"p":
        return pickle.loads(body)
    raise ValueError("unrecognised cache payload")
