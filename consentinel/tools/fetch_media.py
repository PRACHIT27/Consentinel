"""WU-28 — download one media sample so a model can look at it.

A voice-cloning listing sells an audio file. A synthetic endorsement runs as a
video. Text search finds the page that advertises it; only the sample itself
shows whether the offering is real. This fetches the sample.

Everything dangerous about this is already solved next door, so **the guards
are `fetch_page`'s, imported rather than rewritten**: `validate_url` does
scheme checks, DNS resolution and the private-address refusal, and every one of
its tests protects this too. A second SSRF implementation is a second SSRF bug.

Three things it adds, all of them about bytes rather than addresses:

* a **hard size cap**, enforced while streaming rather than after — a caller
  that checks `Content-Length` has already been lied to by anyone who wanted
  to;
* a **content-type allowlist**, because a page that answers `text/html` to a
  request for a sample is not a sample;
* **no storage.** The bytes go to the caller and the record keeps a SHA-256.
  Writing them would need a bucket the web service deliberately cannot write
  to, and a fingerprint is what makes a later answer checkable anyway.

Downloaded media is untrusted in exactly the way a page is, so nothing here
interprets it. That is `MediaTriage`'s job, and it holds no tools.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from consentinel.tools.fetch_page import (
    FetchFailed,
    FetchRefused,
    UnsafeUrl,
    validate_url,
)

TOOL_NAME = "fetch_media"

log = logging.getLogger("consentinel.fetch_media")

# Vertex reads inline bytes up to about 20 MB. Beyond that a sample would have
# to go through the Files API, which is not built — so refuse with a reason
# rather than truncating a file and asking a model to judge half of it.
MAX_BYTES = 18 * 1024 * 1024

ALLOWED_PREFIXES = ("audio/", "video/", "image/")

# Types Gemini will not read. Sending them wastes a call to be told no.
REFUSED_TYPES = frozenset({
    "image/svg+xml",      # markup, not pixels
    "image/x-icon",
    "video/x-ms-asf",
})


@dataclass(frozen=True)
class MediaRef:
    """One downloaded sample. `data` is held in memory and never written."""

    url: str
    mime: str
    sha256: str
    size: int
    data: bytes

    @property
    def kind(self) -> str:
        """audio, video or image — the word the rest of the pipeline uses."""
        return self.mime.split("/", 1)[0]


@dataclass
class MediaFetcher:
    timeout_s: float = 20.0
    max_bytes: int = MAX_BYTES

    def fetch(self, url: str) -> MediaRef:
        """Download one sample, or raise.

        Raises `FetchRefused` for anything we decline to fetch and
        `FetchFailed` for anything that went wrong on the way. The caller
        turns either into a finding that says we could not look — never into
        silence, and never into a pass.
        """
        safe_url, _addresses = validate_url(url)

        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True,
                              headers={"User-Agent": "Consentinel/1.0 (+demo)"}) as client:
                with client.stream("GET", safe_url) as response:
                    if response.status_code >= 400:
                        raise FetchFailed(
                            f"{TOOL_NAME}: HTTP {response.status_code} for {safe_url}")

                    mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if not mime.startswith(ALLOWED_PREFIXES):
                        raise UnsafeUrl(
                            f"{TOOL_NAME}: {safe_url} answered {mime or 'no content type'}, "
                            f"which is not audio, video or an image")
                    if mime in REFUSED_TYPES:
                        raise UnsafeUrl(f"{TOOL_NAME}: {mime} is not something a model can read")

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            # Stop pulling. Checking Content-Length instead
                            # would trust a number the server chose.
                            raise UnsafeUrl(
                                f"{TOOL_NAME}: {safe_url} is larger than "
                                f"{self.max_bytes // (1024 * 1024)} MB")
                        chunks.append(chunk)

        except (FetchRefused, FetchFailed):
            raise
        except httpx.HTTPError as exc:
            raise FetchFailed(f"{TOOL_NAME}: {type(exc).__name__} for {safe_url}") from exc

        data = b"".join(chunks)
        if not data:
            raise FetchFailed(f"{TOOL_NAME}: {safe_url} returned nothing")

        return MediaRef(url=safe_url, mime=mime, sha256=hashlib.sha256(data).hexdigest(),
                        size=len(data), data=data)


def looks_like_media(url: str) -> Optional[str]:
    """A cheap guess from the address, used to order candidates.

    Only a guess: plenty of media sits behind a URL with no extension, and
    plenty of `.mp4` links are landing pages. It decides what to try first, and
    the content type decides what actually happens.
    """
    lowered = url.lower().split("?")[0]
    for suffix, kind in (
        (".mp3", "audio"), (".wav", "audio"), (".m4a", "audio"), (".ogg", "audio"),
        (".flac", "audio"), (".aac", "audio"),
        (".mp4", "video"), (".webm", "video"), (".mov", "video"),
        (".jpg", "image"), (".jpeg", "image"), (".png", "image"), (".webp", "image"),
    ):
        if lowered.endswith(suffix):
            return kind
    return None
