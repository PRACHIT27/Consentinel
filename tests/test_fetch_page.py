"""WU-08 acceptance.

Done when: private-range URLs, `file://` URLs, and a public URL redirecting to
a private address are all rejected. Those three are the first three tests here.

No network and no credentials. HTTP goes through `httpx.MockTransport`, so the
real client code runs — headers, streaming, redirect handling — against a fake
wire. DNS is an injected resolver, because the whole point of the SSRF guard is
what a name resolves *to*.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
import pytest

from consentinel.harness import HarnessDeps, MemoryAudit, MemoryMetrics
from consentinel.harness.ports import CacheHit
from consentinel.store.base import FindingStatus, Locale
from consentinel.tools.contracts import PageSnapshot, UrlRisk
from consentinel.tools.fetch_page import (
    USER_AGENT,
    BlockedAddress,
    FetchFailed,
    FetchRefused,
    PageFetcher,
    UnsafeUrl,
    extract_text,
    is_forbidden_ip,
    validate_url,
)
from consentinel.tools.web_risk import CHECK_FAILED

PT_BR = Locale(language="pt", region="BR")

PAGE = b"""<html><head><title>Voz IA</title>
<style>.x{color:red}</style><script>alert('hi')</script>
<meta property="og:image" content="/og.png"></head>
<body><h1>Clone de voz</h1><p>Compre a voz de Mira Vance por R$ 49.</p>
<img src="/img/demo.jpg"><audio src="https://cdn.example/demo.mp3"></audio>
<noscript>enable javascript</noscript></body></html>"""


# ------------------------------------------------------------------- fakes

class FakeRisk:
    """Stands in for WebRiskCheck. Says safe unless told otherwise."""

    def __init__(self, safe: bool = True, threats: tuple[str, ...] = ()) -> None:
        self.safe = safe
        self.threats = threats
        self.calls: list[str] = []

    def check(self, url: str, *, subject_id: Optional[str] = None) -> UrlRisk:
        self.calls.append(url)
        return UrlRisk(url=url, safe=self.safe, threats=list(self.threats))


def resolver(mapping: Optional[dict[str, str]] = None, default: str = "93.184.216.34"):
    """host -> one IP. Everything unlisted resolves public."""
    table = mapping or {}

    def resolve(host: str) -> list[str]:
        if host in table and table[host] == "NXDOMAIN":
            raise OSError("nxdomain")
        return [table.get(host, default)]

    return resolve


def fetcher(handler=None, *, risk: Optional[FakeRisk] = None,
            hosts: Optional[dict[str, str]] = None, **kw: Any
            ) -> tuple[PageFetcher, FakeRisk, MemoryAudit]:
    def default_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PAGE,
                              headers={"content-type": "text/html; charset=utf-8"})

    risk = risk or FakeRisk()
    audit = MemoryAudit()
    deps = HarnessDeps(audit=audit, metrics=MemoryMetrics())
    kw.setdefault("demo_mode", False)
    kw.setdefault("sleep", lambda _s: None)
    kw.setdefault("max_attempts", 0)
    f = PageFetcher(deps=deps, risk=risk,
                    transport=httpx.MockTransport(handler or default_handler),
                    resolve=resolver(hosts), **kw)
    return f, risk, audit


# ------------------------------------------------ the three acceptance cases

@pytest.mark.parametrize("url", [
    "http://10.0.0.5/admin",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://127.0.0.1:8080/",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata, the classic
    "http://[::1]/",
    "http://[fc00::1]/",
    "http://[fe80::1]/",
    "http://[::ffff:169.254.169.254]/",           # IPv4-mapped dodge
])
def test_private_and_link_local_addresses_are_rejected(url):
    f, _, _ = fetcher()

    outcome = f.fetch_detailed(url, PT_BR)

    assert outcome.refused is True
    assert outcome.status is FindingStatus.BLOCKED_UNSAFE
    assert outcome.snapshot is None
    with pytest.raises(BlockedAddress):
        f.fetch(url, PT_BR)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file://C:/Windows/win.ini",
    "ftp://example.com/x",
    "gopher://example.com/",
    "data:text/html,<script>x</script>",
    "javascript:alert(1)",
    "//example.com/protocol-relative",
])
def test_only_http_and_https_are_allowed(url):
    f, _, _ = fetcher()

    outcome = f.fetch_detailed(url, PT_BR)

    assert outcome.refused is True
    assert "scheme" in (outcome.reason or "") or "no host" in (outcome.reason or "")


def test_a_public_url_redirecting_to_a_private_address_is_rejected():
    """The classic bypass: hop 0 is public, hop 1 is the metadata service.
    Validating only the first URL is how you fall for it."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(302, headers={
                "location": "http://169.254.169.254/latest/meta-data/"})
        raise AssertionError("the private hop must never be requested")

    f, _, _ = fetcher(handler, hosts={"public.example": "93.184.216.34",
                                      "169.254.169.254": "169.254.169.254"})

    outcome = f.fetch_detailed("https://public.example/listing", PT_BR)

    assert outcome.refused is True
    assert outcome.status is FindingStatus.BLOCKED_UNSAFE
    assert "169.254.169.254" in (outcome.reason or "")
    assert outcome.hops == ("https://public.example/listing",
                            "http://169.254.169.254/latest/meta-data/")


def test_a_public_host_that_resolves_privately_is_rejected():
    """DNS rebinding in its laziest form: a public name, a private answer."""
    f, _, _ = fetcher(hosts={"sneaky.example": "10.1.2.3"})

    outcome = f.fetch_detailed("https://sneaky.example/x", PT_BR)

    assert outcome.refused is True
    assert "10.1.2.3" in (outcome.reason or "")


def test_private_names_are_rejected_without_asking_dns():
    for url in ("http://localhost/x", "http://db.internal/x", "http://nas.local/x"):
        assert fetcher()[0].fetch_detailed(url, PT_BR).refused is True


# ------------------------------------------------------- web risk comes first

def test_an_unsafe_address_is_never_opened():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PAGE)

    f, risk, _ = fetcher(handler, risk=FakeRisk(safe=False, threats=("MALWARE",)))

    outcome = f.fetch_detailed("https://bad.example/listing", PT_BR)

    assert outcome.refused is True
    assert outcome.status is FindingStatus.BLOCKED_UNSAFE
    assert "MALWARE" in (outcome.reason or "")
    assert calls == []                       # no HTTP request at all
    assert risk.calls == ["https://bad.example/listing"]
    with pytest.raises(UnsafeUrl):
        f.fetch("https://bad.example/listing", PT_BR)


def test_the_risk_check_runs_before_address_validation():
    """Order matters: a dangerous address gets refused for being dangerous, not
    for where it resolves, so the reason on the finding is the true one."""
    f, risk, _ = fetcher(risk=FakeRisk(safe=False, threats=("SOCIAL_ENGINEERING",)),
                         hosts={"bad.example": "10.0.0.9"})

    outcome = f.fetch_detailed("https://bad.example/x", PT_BR)

    assert "SOCIAL_ENGINEERING" in (outcome.reason or "")
    assert risk.calls == ["https://bad.example/x"]


def test_a_failed_risk_check_fails_closed():
    """Contract v2.0.0: if the check itself errors, treat the address as unsafe.
    One missed listing costs nothing; opening a malware page costs more."""
    f, _, _ = fetcher(risk=FakeRisk(safe=False, threats=(CHECK_FAILED,)))

    outcome = f.fetch_detailed("https://unknown.example/x", PT_BR)

    assert outcome.refused is True
    assert CHECK_FAILED in (outcome.reason or "")


def test_the_check_can_be_disabled_but_says_so_loudly(monkeypatch, caplog):
    monkeypatch.setenv("WEB_RISK_ENABLED", "false")
    f, risk, _ = fetcher()

    with caplog.at_level(logging.WARNING, logger="consentinel.fetch_page"):
        outcome = f.fetch_detailed("https://shop.example/x", PT_BR)

    assert outcome.ok is True
    assert risk.calls == []
    assert any("unchecked" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- the fetch

def test_a_clean_fetch_returns_a_page_snapshot():
    f, _, _ = fetcher()

    snapshot = f.fetch("https://loja.example/mira", PT_BR)

    assert isinstance(snapshot, PageSnapshot)
    assert "Compre a voz de Mira Vance" in snapshot.text
    assert snapshot.from_cache is False
    assert snapshot.fetched_at is not None


def test_scripts_styles_and_noscript_never_reach_the_text():
    """We never render fetched HTML, and the tags come off on the way in."""
    f, _, _ = fetcher()

    text = f.fetch("https://loja.example/mira", PT_BR).text

    assert "alert(" not in text
    assert "color:red" not in text
    assert "enable javascript" not in text
    assert "<" not in text and ">" not in text


def test_media_references_are_collected_and_made_absolute():
    f, _, _ = fetcher()

    snapshot = f.fetch("https://loja.example/mira", PT_BR)

    assert "https://loja.example/img/demo.jpg" in snapshot.media_refs
    assert "https://cdn.example/demo.mp3" in snapshot.media_refs
    assert "https://loja.example/og.png" in snapshot.media_refs


def test_no_cookies_or_credentials_are_sent_and_the_agent_is_declared():
    """DESIGN 4.4. If we sent a token to a hostile page, that page would then
    have our token."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, content=b"<p>ok</p>",
                              headers={"content-type": "text/html"})

    f, _, _ = fetcher(handler)
    f.fetch("https://loja.example/mira", PT_BR)

    assert "cookie" not in seen
    assert "authorization" not in seen
    assert seen["user-agent"] == USER_AGENT
    assert seen["accept-language"] == "pt-BR,pt;q=0.9"   # locale on the wire


def test_an_oversized_body_is_truncated_at_the_cap():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<p>" + b"a" * 50_000,
                              headers={"content-type": "text/html"})

    f, _, _ = fetcher(handler, max_bytes=1_000)

    outcome = f.fetch_detailed("https://loja.example/big", PT_BR)

    assert outcome.truncated is True
    assert "truncated at 1000 bytes" in outcome.snapshot.text
    assert len(outcome.snapshot.text) < 2_000


def test_a_redirect_chain_that_never_ends_is_abandoned():
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("n", "0"))
        return httpx.Response(302, headers={
            "location": f"https://loop.example/?n={n + 1}"})

    f, _, _ = fetcher(handler, max_redirects=3)

    outcome = f.fetch_detailed("https://loop.example/?n=0", PT_BR)

    assert outcome.ok is False
    assert outcome.refused is False          # not a refusal — it simply failed
    assert "redirects" in (outcome.reason or "")


def test_a_redirect_to_another_public_page_is_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={
                "location": "https://loja.example/new"})
        return httpx.Response(200, content=b"<p>moved here</p>",
                              headers={"content-type": "text/html"})

    f, _, _ = fetcher(handler)

    outcome = f.fetch_detailed("https://loja.example/old", PT_BR)

    assert outcome.ok is True
    assert "moved here" in outcome.snapshot.text
    assert outcome.snapshot.url.endswith("/new")
    assert len(outcome.hops) == 2


def test_non_text_content_yields_no_text_and_records_the_media_address():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00\x01binary",
                              headers={"content-type": "audio/mpeg"})

    f, _, _ = fetcher(handler)

    snapshot = f.fetch("https://cdn.example/demo.mp3", PT_BR)

    assert snapshot.text == ""
    assert snapshot.media_refs == ["https://cdn.example/demo.mp3"]


def test_plain_text_is_taken_as_is():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="voz sintética de Mira".encode(),
                              headers={"content-type": "text/plain; charset=utf-8"})

    f, _, _ = fetcher(handler)

    assert f.fetch("https://loja.example/robots", PT_BR).text == "voz sintética de Mira"


# ------------------------------------------------------------- error handling

def test_a_404_is_not_retried_but_a_503_is():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(404 if request.url.path == "/gone" else 503)

    f, _, _ = fetcher(handler, max_attempts=2)
    f.fetch_detailed("https://loja.example/gone", PT_BR)
    gone = len(attempts)
    attempts.clear()
    f.fetch_detailed("https://loja.example/flaky", PT_BR)

    assert gone == 1                     # permanent: recorded and dropped
    assert len(attempts) == 3            # transient: first try plus two retries


def test_a_network_error_fails_rather_than_refuses():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset")

    f, _, _ = fetcher(handler)

    outcome = f.fetch_detailed("https://loja.example/x", PT_BR)

    assert outcome.failed is True
    assert outcome.refused is False      # we tried; we did not decline
    assert outcome.status is None
    with pytest.raises(FetchFailed):
        f.fetch("https://loja.example/x", PT_BR)


def test_refusal_and_failure_are_different_exception_families():
    """A caller must be able to tell "we declined to look" from "we could not"."""
    assert issubclass(UnsafeUrl, FetchRefused)
    assert issubclass(BlockedAddress, FetchRefused)
    assert not issubclass(FetchFailed, FetchRefused)


# ------------------------------------------------------------------- caching

def test_the_second_fetch_is_served_from_cache_with_an_honest_age():
    """WU-08: set from_cache and cache_age_seconds honestly — the audit trail
    depends on it."""
    class Cache(dict):
        def get(self, key):
            from datetime import datetime, timedelta, timezone
            if key not in self:
                return None
            return CacheHit(value=self[key],
                            fetched_at=datetime.now(timezone.utc) - timedelta(seconds=90))

        def put(self, key, value, ttl):
            self[key] = value

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PAGE,
                              headers={"content-type": "text/html"})

    audit = MemoryAudit()
    f, _, _ = fetcher(handler)
    f.deps = HarnessDeps(cache=Cache(), audit=audit)
    f._harness = None

    f.fetch_detailed("https://loja.example/mira", PT_BR)
    second = f.fetch_detailed("https://loja.example/mira", PT_BR)

    assert len(calls) == 1
    assert second.from_cache is True
    assert second.cache_age_s is not None and second.cache_age_s >= 90
    row = [e for e in audit.events if e.get("actor") == "fetch_page"][-1]
    assert row["from_cache"] is True and row["cache_age_s"] >= 90


def test_locale_is_part_of_the_cache_key():
    """Hard rule 8, and it is not academic: the same URL returns different
    pages with a different Accept-Language."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("accept-language", ""))
        return httpx.Response(200, content=PAGE,
                              headers={"content-type": "text/html"})

    class Cache(dict):
        def get(self, key):
            from datetime import datetime, timezone
            return (CacheHit(value=self[key], fetched_at=datetime.now(timezone.utc))
                    if key in self else None)

        def put(self, key, value, ttl):
            self[key] = value

    f, _, _ = fetcher(handler)
    f.deps = HarnessDeps(cache=Cache(), audit=MemoryAudit())
    f._harness = None

    f.fetch_detailed("https://loja.example/mira", PT_BR)
    f.fetch_detailed("https://loja.example/mira", Locale("en", "US"))

    assert calls == ["pt-BR,pt;q=0.9", "en-US,en;q=0.9"]


def test_demo_mode_refuses_to_reach_the_network_on_a_miss():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PAGE)

    f, _, _ = fetcher(handler, demo_mode=True)

    outcome = f.fetch_detailed("https://loja.example/mira", PT_BR)

    assert calls == []
    assert outcome.ok is False
    assert "DEMO_MODE" in (outcome.reason or "")


# --------------------------------------------------------------- audit trail

def test_every_fetch_writes_one_audit_row_refusals_included():
    f, _, audit = fetcher(risk=FakeRisk(safe=False, threats=("MALWARE",)))
    f.fetch_detailed("https://bad.example/x", PT_BR, subject_id="perf_mira")

    rows = [e for e in audit.events if e.get("actor") == "fetch_page"]
    assert len(rows) == 1
    assert rows[0]["refused"] is True
    assert rows[0]["status"] == "blocked_unsafe"
    assert rows[0]["subject_id"] == "perf_mira"
    assert rows[0]["web_risk_safe"] is False


def test_the_log_line_reads_at_a_glance(caplog):
    f, _, _ = fetcher()

    with caplog.at_level(logging.INFO, logger="consentinel.fetch_page"):
        f.fetch("https://loja.example/mira", PT_BR)

    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("fetch_page url="))
    assert "locale=pt-BR" in line and "hops=1" in line and "status=200" in line
    assert "chars=" in line and "cache=miss" in line


# ------------------------------------------------------------------ helpers

def test_is_forbidden_ip_covers_the_ranges_wu_08_names():
    for private in ("10.0.0.1", "172.16.5.5", "192.168.0.1", "127.0.0.1",
                    "169.254.169.254", "::1", "fe80::1", "fc00::1",
                    "100.64.0.1", "0.0.0.0", "224.0.0.1"):
        assert is_forbidden_ip(private) is True, private
    for public in ("93.184.216.34", "8.8.8.8", "2606:2800:220:1::248:1893"):
        assert is_forbidden_ip(public) is False, public
    assert is_forbidden_ip("not-an-ip") is True      # unparseable: refuse


def test_validate_url_returns_the_host_and_addresses_it_checked():
    host, addresses = validate_url("https://loja.example/x", resolver())

    assert host == "loja.example"
    assert addresses == ["93.184.216.34"]


def test_a_host_with_one_private_address_is_rejected_entirely():
    """Half-public is not half-safe: which address gets connected to is not
    ours to choose."""
    def resolve(_host: str) -> list[str]:
        return ["93.184.216.34", "10.0.0.7"]

    with pytest.raises(BlockedAddress):
        validate_url("https://mixed.example/x", resolve)


def test_an_unresolvable_host_is_refused_not_crashed():
    with pytest.raises(BlockedAddress):
        validate_url("https://nope.example/x", resolver({"nope.example": "NXDOMAIN"}))


def test_extract_text_survives_broken_markup():
    text, media = extract_text("<p>hello <b>world<img src='/x.png'>", "https://e.example/")

    assert "hello world" in text
    assert media == ["https://e.example/x.png"]


def test_contract_signature_is_unchanged():
    import inspect
    from importlib import import_module

    from consentinel.tools import contracts
    module = import_module("consentinel.tools.fetch_page")

    assert list(inspect.signature(module.fetch_page).parameters) == \
           list(inspect.signature(contracts.fetch_page).parameters)
