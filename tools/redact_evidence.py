"""Turn a live evidence pack into one we can publish.

`COMPETITION.md` §6 requires "evidence of runtime use of Google Cloud and
Parallel (screenshots + the call log)", and §11 lists "Parallel only mentioned,
not called" as a thing that would sink us. We have that evidence — a real
`--fresh` sweep in `evidence/runtime/` — but the directory is gitignored, so a
judge reading the repo currently sees none of it.

It is gitignored for a good reason. CLAUDE.md's demo-safety rule:

    we do not publish authorisation verdicts about real people or name real
    third-party sites on camera

The live sweep hit seventeen real hosts. So the pack cannot ship as-is, and it
cannot ship gutted either, because the thing that proves the integration is
real is precisely its texture: eight distinct `search_id`s, every one
`cache=miss`, five locales, honest latencies.

The split falls out neatly, because of where URLs actually occur:

- **The `parallel_search` lines contain no URL at all** — they log the query,
  locale, mode, result count, latency and `search_id`. They pass through this
  tool completely untouched, which is exactly the log §4 asks for.
- Everything downstream (`web_risk_check`, `fetch_page`, `Triage`) is keyed by
  the URL it was given, so every one of those lines names a real host.

What this does, then, is replace each real URL with a stable pseudonym and
leave every other byte alone. Stable matters: the same host keeps the same
pseudonym across all six actors and both files, so the log still reads as a
pipeline trace — you can follow one candidate from `web_risk_check` through
`fetch_page` into `Triage` and see the verdict it produced.

Two details worth knowing:

- **URLs are redacted anywhere in the text, not just in `url` fields.** They
  turn up inside failure messages too — `"FetchFailed: fetch_page: HTTP 404 for
  https://..."` — and a field-by-field redactor would have published those.
- **The salt is random per run** and is not written into the output. A six-hex
  digest of a bare hostname is not redaction; the set of AI voice-cloning
  marketplaces is small enough to enumerate and check against. Pass `--salt`
  to reproduce a previous pack.

No page text is at risk here: quotes are logged as the booleans `has_quote` /
`quote=False`, never as content.

    python tools/redact_evidence.py
    python tools/redact_evidence.py --in evidence/runtime --out evidence/published
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_IN = Path("evidence/runtime")
DEFAULT_OUT = Path("evidence/published")

URL = re.compile(r"https?://[^\s\"'\]<>,)]+")
"""Deliberately greedy about the path and loose about the delimiter set.

Over-matching gets redacted, which is the safe direction. Under-matching
publishes a hostname.
"""

_TRAILING = ".,;:!?"
"""Sentence punctuation that a greedy URL match will swallow. A trailing `/` is
part of the URL and stays."""

REDACTED_TLD = "redacted"
"""Not a real TLD, so a pseudonym can never be mistaken for a resolvable host
or accidentally visited by a reader."""

_SAFE_HOSTS = frozenset({
    "example.com", "example.org", "example.net",
    "example-marketplace.invalid", "example-loja.invalid",
    "example-fanart.invalid", "localhost",
})
"""Hosts that are already fictional — the seed fixtures and the planted demo
pages. Redacting these would hide the parts of the trace we *want* read."""


def _digest(value: str, salt: str, width: int = 6) -> str:
    return hashlib.sha256(f"{salt}\x00{value}".encode()).hexdigest()[:width]


@dataclass
class Redactor:
    """Replaces real URLs with stable pseudonyms.

    One instance per pack. `hosts` accumulates the mapping so the summary can
    report how many distinct hosts were involved without naming any of them.
    """

    salt: str
    hosts: dict[str, str] = field(default_factory=dict)
    urls: dict[str, str] = field(default_factory=dict)

    def host_alias(self, host: str) -> str:
        if host in _SAFE_HOSTS:
            return host
        if host not in self.hosts:
            self.hosts[host] = f"host-{_digest(host, self.salt)}.{REDACTED_TLD}"
        return self.hosts[host]

    def url_alias(self, url: str) -> str:
        """`https://host/a/b?c=d` -> `https://host-ab12cd.redacted/p-ef3456`.

        The path collapses to a single opaque segment. Keeping its shape would
        leak the host almost as readily as naming it — `/ai-voice-generator/mira`
        is not much of a disguise.
        """
        if url in self.urls:
            return self.urls[url]

        match = re.match(r"(https?)://([^/?#]+)(.*)", url)
        if match is None:                     # not a URL after all; leave it
            return url
        scheme, host, rest = match.groups()

        alias_host = self.host_alias(host)
        if alias_host == host:                # already fictional, keep verbatim
            alias = url
        elif rest in ("", "/"):
            alias = f"{scheme}://{alias_host}{rest}"
        else:
            alias = f"{scheme}://{alias_host}/p-{_digest(rest, self.salt)}"

        self.urls[url] = alias
        return alias

    def text(self, value: str) -> str:
        """Every URL anywhere in the string."""
        def replace(match: re.Match[str]) -> str:
            url = match.group(0)
            tail = ""
            while url and url[-1] in _TRAILING:
                url, tail = url[:-1], url[-1] + tail
            return self.url_alias(url) + tail

        return URL.sub(replace, value)

    def value(self, value: Any) -> Any:
        """The same, through a nested JSON structure."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        return value


# --------------------------------------------------------------------------
# The two file formats
# --------------------------------------------------------------------------

def redact_calls_log(lines: Iterable[str], redactor: Redactor) -> list[str]:
    return [redactor.text(line.rstrip("\n")) for line in lines]


def redact_audit_jsonl(lines: Iterable[str], redactor: Redactor) -> list[str]:
    out: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        row = redactor.value(json.loads(line))
        out.append(json.dumps(row, ensure_ascii=False, sort_keys=False))
    return out


# --------------------------------------------------------------------------
# The summary a judge reads first
# --------------------------------------------------------------------------

@dataclass
class Summary:
    """What the pack proves, counted rather than asserted.

    Every figure here is derived from the redacted lines. The first draft of
    this class counted cache misses across all six actors and the README then
    claimed the number of the Parallel calls specifically — which was wrong,
    and wrong in the flattering direction. Parallel's cache state is now
    counted separately from everyone else's, because that is the one number a
    judge is most likely to check.
    """

    parallel_live: int = 0
    """`cache=miss` — a real HTTP call to Parallel happened."""

    parallel_cached: int = 0
    """`cache=hit` — served from the warm cache. Not evidence the integration
    ran, but evidence `DEMO_MODE` does, which the shoot depends on."""

    live_search_ids: list[str] = field(default_factory=list)
    cached_search_ids: list[str] = field(default_factory=list)
    locales: list[str] = field(default_factory=list)
    calls_by_actor: Counter = field(default_factory=Counter)
    audit_rows: int = 0
    hosts_redacted: int = 0
    urls_redacted: int = 0

    @property
    def parallel_calls(self) -> int:
        return self.parallel_live + self.parallel_cached

    @property
    def distinct_live_ids(self) -> int:
        return len(set(self.live_search_ids))


_KV = re.compile(r"(\w+)=(\S+)")


def summarise(calls: Iterable[str], audit_rows: Iterable[str],
              redactor: Redactor) -> Summary:
    """Read the *redacted* lines, so the summary can never leak what the
    redactor caught."""
    s = Summary()

    for line in calls:
        fields = dict(_KV.findall(line))
        parts = line.split()
        if len(parts) > 3 and parts[3].startswith("consentinel."):
            s.calls_by_actor[parts[3].removeprefix("consentinel.")] += 1
        if "search_id" in fields:
            if fields.get("cache") == "miss":
                s.parallel_live += 1
                s.live_search_ids.append(fields["search_id"])
            else:
                s.parallel_cached += 1
                s.cached_search_ids.append(fields["search_id"])
            if "locale" in fields:
                s.locales.append(fields["locale"])

    for line in audit_rows:
        s.audit_rows += 1

    s.hosts_redacted = len(redactor.hosts)
    s.urls_redacted = len(redactor.urls)
    return s


def render_readme(s: Summary, packs: list[str]) -> str:
    """The file that turns a log into evidence.

    Written for someone with no context who has three minutes: what this is,
    what is real, what was changed and why.
    """
    ids = "\n".join(f"    {i}" for i in sorted(set(s.live_search_ids)))
    locales = ", ".join(sorted(set(s.locales)))
    actors = "\n".join(f"| `{a}` | {n} |"
                       for a, n in sorted(s.calls_by_actor.items()))
    cached = (
        f"\nA further **{s.parallel_cached} calls were served `cache=hit`** from an earlier "
        f"run's warm cache. Those are not evidence the integration ran — they are\n"
        f"evidence `DEMO_MODE` does, which is what lets the demo be recorded "
        f"without\ndepending on a live rate limit.\n"
    ) if s.parallel_cached else ""

    return f"""# Runtime evidence — Parallel Search and Google Cloud

Generated by `tools/redact_evidence.py` from live runs of the discovery
pipeline. Source packs: {", ".join(packs)}.

This is the log `COMPETITION.md` §4 asks for. Nothing here is a fixture or a
replay, and the `cache=` field on every line is what makes that checkable
rather than a claim.

## Parallel Search

**{s.parallel_live} calls went to Parallel over the wire** — `cache=miss`, with the
latency each one took — returning {s.distinct_live_ids} distinct `search_id`s.
Locales swept: {locales}.

The `search_id`s are Parallel's own, returned by `client.search(...)` from the
official `parallel-web` SDK. They are reproduced verbatim:

{ids}
{cached}
Every `parallel_search` line in `*-calls.log` is **byte-for-byte as it was
logged** — those lines contain no URL, so the redaction below never touched
them. Each one carries the queries as sent (in the locale's own language), the
`location` code, the mode, the result count, the attempt count and the real
latency.

## The rest of the pipeline

{sum(s.calls_by_actor.values())} logged calls across {len(s.calls_by_actor)} actors, and
{s.audit_rows} audit rows in `*-audit.jsonl`:

| Actor | Logged calls |
|---|---|
{actors}

## What was changed, and why

CLAUDE.md's demo-safety rule: *we do not publish authorisation verdicts about
real people or name real third-party sites on camera.* The live sweep read
**{s.hosts_redacted} real hosts**, so every URL in the downstream lines — {s.urls_redacted}
distinct ones — was replaced with a stable pseudonym of the form
`https://host-xxxxxx.redacted/p-yyyyyy`.

Stable is the point. One host keeps one pseudonym across all actors and both
packs, so a candidate can still be followed from `web_risk_check` through
`fetch_page` into `Triage` and out to its verdict. The pseudonyms are salted
with a value that was not recorded, so they cannot be checked back against a
list of candidate hosts.

Fictional hosts are **not** redacted: the seeded fixtures and the planted
injection page keep their real (`.invalid`) names, because those are the parts
of the trace we want read.

Nothing else was altered. No line was dropped, reordered or summarised. Page
text was never at risk — evidence quotes are logged as the booleans
`has_quote` / `quote=False`, never as content.
"""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def run(src: Path, dst: Path, salt: Optional[str] = None) -> Summary:
    packs = sorted({p.name.split("-")[0] for p in src.glob("*-calls.log")})
    if not packs:
        raise SystemExit(
            f"no evidence packs in {src}. Produce one first:\n"
            f"    python tools/warm_demo_cache.py --fresh --evidence-dir {src}")

    redactor = Redactor(salt=salt or secrets.token_hex(16))
    dst.mkdir(parents=True, exist_ok=True)

    all_calls: list[str] = []
    all_audit: list[str] = []

    for path in sorted(src.glob("*-calls.log")):
        lines = redact_calls_log(path.read_text(encoding="utf-8").splitlines(),
                                 redactor)
        (dst / path.name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        all_calls += lines

    for path in sorted(src.glob("*-audit.jsonl")):
        rows = redact_audit_jsonl(path.read_text(encoding="utf-8").splitlines(),
                                  redactor)
        (dst / path.name).write_text("\n".join(rows) + "\n", encoding="utf-8")
        all_audit += rows

    summary = summarise(all_calls, all_audit, redactor)
    (dst / "README.md").write_text(render_readme(summary, packs),
                                   encoding="utf-8")
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--in", dest="src", type=Path, default=DEFAULT_IN)
    parser.add_argument("--out", dest="dst", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--salt", default=None,
                        help="reproduce a previous pack's pseudonyms")
    args = parser.parse_args(argv)

    s = run(args.src, args.dst, args.salt)

    print(f"{args.dst}:")
    print(f"  Parallel: {s.parallel_live} live (cache=miss), "
          f"{s.parallel_cached} cached, "
          f"{s.distinct_live_ids} distinct live search_ids")
    print(f"  {sum(s.calls_by_actor.values())} logged calls across "
          f"{len(s.calls_by_actor)} actors, {s.audit_rows} audit rows")
    print(f"  {s.hosts_redacted} hosts / {s.urls_redacted} URLs pseudonymised")
    return 0


if __name__ == "__main__":
    sys.exit(main())
