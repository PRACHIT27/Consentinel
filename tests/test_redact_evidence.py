"""WU-37 — the evidence pack we can actually publish.

Done when: the pack proves the Parallel integration ran, and names no real
third-party host. Those are the first two tests; everything after them defends
a way the first two could quietly stop being true.

The failure that matters here is one-directional. A pseudonym that leaks a real
hostname breaks CLAUDE.md's demo-safety rule in a public repo, and deleting it
in a later commit does not un-publish it. So the tests below are written as
"nothing survives" assertions over the whole output, not spot checks on the
fields we happened to think of.
"""

from __future__ import annotations

import json

import pytest

from tools.redact_evidence import (
    REDACTED_TLD,
    Redactor,
    redact_audit_jsonl,
    redact_calls_log,
    render_readme,
    run,
    summarise,
)

REAL_HOSTS = ("voice-forge.test", "www.clonemarket.test", "synth.test",
              "www.example-studio.test", "labs.voxcopy.test")
"""Stand-ins for the hosts the live sweep hit — **not** the hosts themselves.

The first draft of this file pasted in the five real hostnames, on the
reasoning that a test about redacting real hosts should use real hosts. That
put them in a public repo, which is the exact thing the module under test
exists to prevent, and a later commit does not un-publish them.

`.test` is reserved by RFC 2606 and cannot be registered, so these can never
collide with a real site. They exercise the identical path: nothing here is in
`_SAFE_HOSTS`, so the redactor treats them exactly as it treats a real host.
Do not "improve" this by putting the real ones back.
"""

PARALLEL_LINE = (
    "2026-09-09 19:54:32,538 INFO consentinel.parallel_search parallel_search "
    "sdk=parallel-web api=search mode=basic locale=es-MX location=mx "
    "cache=miss results=3 latency_ms=2484 attempts=1 "
    "search_id=search_39b9f65be61e5d2cae57968677c78c50 "
    "session_id=sweep_05b652d06eb9 "
    'queries=["Mira Vance clon de voz IA", "Mira Vance voz sintética"]'
)

CALLS = [
    PARALLEL_LINE,
    "2026-09-09 19:54:41,026 INFO consentinel.web_risk web_risk_check "
    "url=https://www.clonemarket.test/ai-voice-generator/mira safe=True "
    "threats=[] cache=miss",
    "2026-09-09 19:54:42,949 INFO consentinel.fetch_page fetch_page "
    "url=https://www.clonemarket.test/ai-voice-generator/mira locale=en-US "
    "hops=1 status=200 chars=2308 media=35 cache=miss latency_ms=1187",
    "2026-09-09 19:55:36,888 WARNING consentinel.triage Triage "
    "url=https://voice-forge.test/ai-voice-cloning ok=False confidence=- "
    'quote=False injection=False cache=miss repairs=1 reason="FetchFailed: '
    'fetch_page: HTTP 404 for https://synth.test/m/a95cf78ba311400181f2c30.'
    '"',
    "2026-09-09 19:55:47,738 INFO consentinel.reconciler Reconciler "
    "performer=perf_mira_vance verdict=ambiguous "
    "check=performer_not_identified grants=1 citation=False",
]

AUDIT = [
    json.dumps({"ts": "2026-09-09T14:24:30.081299+00:00",
                "actor": "parallel_search", "subject_id": "perf_mira_vance",
                "ok": True, "search_id": "search_39b9f65b", "locale": "es-MX",
                "tool_calls": [{"from_cache": False, "attempts": 1}]}),
    json.dumps({"ts": "2026-09-09T14:24:40.0+00:00", "actor": "fetch_page",
                "url": "https://www.clonemarket.test/ai-voice-generator/mira",
                "final_url": "https://www.clonemarket.test/ai-voice-generator/mira",
                "http_status": 200, "ok": True,
                "reason": None}),
    json.dumps({"ts": "2026-09-09T14:24:50.0+00:00", "actor": "fetch_page",
                "url": "https://www.example-studio.test/es_mx/funciones/clonador-voz-ia/",
                "ok": False,
                "reason": "FetchFailed: fetch_page: HTTP 403 for "
                          "https://www.example-studio.test/es_mx/funciones/clonador-voz-ia/"}),
]


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(salt="test-salt")


def redact_all(redactor: Redactor) -> str:
    """Both files, redacted, as one blob to assert over."""
    return "\n".join(redact_calls_log(CALLS, redactor)
                     + redact_audit_jsonl(AUDIT, redactor))


# ------------------------------------------------------- the acceptance tests

def test_no_real_host_survives_anywhere_in_the_output(redactor):
    """The rule this whole module exists to keep: no real third-party site is
    named. Asserted over the entire output rather than field by field, because
    URLs turn up inside failure messages too."""
    out = redact_all(redactor)

    for host in REAL_HOSTS:
        assert host not in out, f"{host} survived redaction"


def test_the_parallel_lines_pass_through_byte_for_byte(redactor):
    """The evidence itself. `COMPETITION.md` §4 wants the call log, and a
    redacted `search_id` or a rewritten query would prove nothing.

    This works because those lines contain no URL — so it is a property of the
    log format, not a special case in the redactor, and this test is what
    notices if that stops being true.
    """
    assert redact_calls_log([PARALLEL_LINE], redactor) == [PARALLEL_LINE]


# ------------------------------------------------------------ how it redacts

def test_a_url_inside_a_failure_message_is_redacted_too(redactor):
    """The near miss. A field-by-field redactor passes `url` and `final_url`
    and publishes the hostname sitting inside `reason`."""
    rows = [json.loads(r) for r in redact_audit_jsonl(AUDIT, redactor)]
    failed = next(r for r in rows if r["ok"] is False)

    assert "example-studio.test" not in failed["reason"]
    assert REDACTED_TLD in failed["reason"]
    assert failed["reason"].startswith("FetchFailed: fetch_page: HTTP 403 for ")


def test_one_host_keeps_one_pseudonym_across_actors_and_formats(redactor):
    """Stability is what keeps the log readable as a pipeline trace: the same
    candidate has to be followable from web_risk through fetch_page to a
    verdict."""
    calls = redact_calls_log(CALLS, redactor)
    audit = [json.loads(r) for r in redact_audit_jsonl(AUDIT, redactor)]

    from_web_risk = calls[1].split("url=")[1].split()[0]
    from_fetch = calls[2].split("url=")[1].split()[0]
    from_audit = next(r["url"] for r in audit if r["actor"] == "fetch_page")

    assert from_web_risk == from_fetch == from_audit


def test_the_path_does_not_survive_in_recognisable_form(redactor):
    """`/ai-voice-generator/mira` identifies the host nearly as well as its
    name does."""
    out = redact_all(redactor)

    assert "ai-voice-generator" not in out
    assert "clonador-voz-ia" not in out
    assert "/p-" in out


def test_distinct_hosts_get_distinct_pseudonyms(redactor):
    """Collapsing them would understate the sweep's reach and make the trace
    unreadable."""
    aliases = {redactor.url_alias(f"https://{h}/x") for h in REAL_HOSTS}

    assert len(aliases) == len(REAL_HOSTS)


def test_fictional_hosts_are_left_alone(redactor):
    """The seed fixtures and the planted injection page are the parts of the
    trace we want read. Redacting them would hide the demo."""
    for url in ("https://example-marketplace.invalid/listing/mira-vance-voice",
                "https://example-loja.invalid/anuncio/voz-ia-mira-vance",
                "https://example-fanart.invalid/gallery/vance-poster"):
        assert redactor.url_alias(url) == url


def test_sentence_punctuation_is_not_swallowed_into_the_pseudonym(redactor):
    """A greedy URL match eats the full stop that ends the sentence."""
    out = redactor.text("HTTP 404 for https://synth.test/m/abc.")

    assert out.endswith(".")
    assert "synth.test" not in out


def test_a_trailing_slash_is_kept_because_it_is_part_of_the_url(redactor):
    assert redactor.url_alias("https://voice-forge.test/").endswith("/")


def test_the_pseudonym_tld_cannot_resolve(redactor):
    """A reader must not be able to visit a pseudonym by accident, or mistake
    one for a live site."""
    alias = redactor.url_alias("https://voice-forge.test/ai-voice-cloning")

    assert f".{REDACTED_TLD}/" in alias
    assert REDACTED_TLD not in ("com", "ai", "io", "net", "org")


def test_a_different_salt_gives_different_pseudonyms():
    """The salt is the actual protection. Six hex digits of an unsalted
    hostname is not redaction — the set of AI voice-cloning marketplaces is
    small enough to enumerate and check against."""
    a = Redactor(salt="one").url_alias("https://voice-forge.test/x")
    b = Redactor(salt="two").url_alias("https://voice-forge.test/x")

    assert a != b


def test_the_same_salt_reproduces_a_pack():
    """`--salt` has to actually work, or a regenerated pack silently stops
    correlating with the screenshots already in the video."""
    a = Redactor(salt="same").url_alias("https://voice-forge.test/x")
    b = Redactor(salt="same").url_alias("https://voice-forge.test/x")

    assert a == b


def test_nothing_that_is_not_a_url_is_touched(redactor):
    """Over-redaction is the safe direction but still costs evidence."""
    line = ("performer=perf_mira_vance verdict=ambiguous "
            "check=performer_not_identified grants=1 citation=False")

    assert redactor.text(line) == line


# ------------------------------------------------------- structural integrity

def test_no_line_is_dropped_or_reordered(redactor):
    assert len(redact_calls_log(CALLS, redactor)) == len(CALLS)
    assert len(redact_audit_jsonl(AUDIT, redactor)) == len(AUDIT)


def test_every_audit_row_is_still_valid_json_with_its_keys_intact(redactor):
    before = [json.loads(r) for r in AUDIT]
    after = [json.loads(r) for r in redact_audit_jsonl(AUDIT, redactor)]

    for b, a in zip(before, after):
        assert set(b) == set(a)
        assert a["ts"] == b["ts"] and a["actor"] == b["actor"]


def test_non_string_values_come_through_unchanged(redactor):
    """Latencies, statuses and booleans are the measurements. Coercing one to a
    string would make the pack look doctored."""
    row = {"http_status": 200, "ok": False, "latency_s": 1.187,
           "threats": [], "cache_age_s": None,
           "tool_calls": [{"from_cache": False, "attempts": 1}]}

    assert redactor.value(row) == row


# -------------------------------------------------------------- the summary

def test_the_summary_counts_the_parallel_calls_it_can_see(redactor):
    calls = redact_calls_log(CALLS, redactor)
    audit = redact_audit_jsonl(AUDIT, redactor)

    s = summarise(calls, audit, redactor)

    assert s.parallel_live == 1
    assert s.distinct_live_ids == 1
    assert s.audit_rows == len(AUDIT)
    assert s.calls_by_actor["parallel_search"] == 1
    assert s.hosts_redacted == 4        # clonemarket, voice-forge, synth, example-studio


def test_a_cached_parallel_call_is_never_counted_as_a_live_one():
    """The mistake the first draft made, and it flattered us.

    Cache misses were counted across all six actors and the README then
    claimed the figure of the Parallel calls specifically. A pack whose
    Parallel calls all came from cache would have advertised them as live —
    which is the exact claim `COMPETITION.md` §11 says would sink us.
    """
    cached = PARALLEL_LINE.replace("cache=miss", "cache=hit")

    s = summarise([cached], [], Redactor(salt="x"))

    assert s.parallel_live == 0
    assert s.parallel_cached == 1
    assert s.parallel_calls == 1


def test_the_readme_does_not_advertise_a_cached_call_as_a_live_one():
    cached = PARALLEL_LINE.replace("cache=miss", "cache=hit")

    readme = render_readme(summarise([cached], [], Redactor(salt="x")), ["p"])

    assert "**0 calls went to Parallel over the wire**" in readme
    assert "search_39b9f65be61e5d2cae57968677c78c50" not in readme
    assert "cache=hit" in readme


def test_the_summary_reads_the_redacted_lines_so_it_cannot_leak(redactor):
    """The README is generated from the output, never from the source, so a
    summary can never re-publish what the redactor just caught."""
    calls = redact_calls_log(CALLS, redactor)
    audit = redact_audit_jsonl(AUDIT, redactor)

    readme = render_readme(summarise(calls, audit, redactor), ["20260909T142427Z"])

    for host in REAL_HOSTS:
        assert host not in readme


def test_the_readme_quotes_the_real_search_ids(redactor):
    """The one thing in the pack a judge is most likely to check, and the one
    thing that must not be pseudonymised."""
    calls = redact_calls_log(CALLS, redactor)
    readme = render_readme(summarise(calls, [], redactor), ["20260909T142427Z"])

    assert "search_39b9f65be61e5d2cae57968677c78c50" in readme
    assert "cache=miss" in readme


# -------------------------------------------------------------------- the CLI

def test_run_writes_a_pack_and_a_readme(tmp_path):
    src, dst = tmp_path / "runtime", tmp_path / "published"
    src.mkdir()
    (src / "20260909T142427Z-calls.log").write_text("\n".join(CALLS),
                                                    encoding="utf-8")
    (src / "20260909T142427Z-audit.jsonl").write_text("\n".join(AUDIT),
                                                      encoding="utf-8")

    summary = run(src, dst, salt="fixed")

    assert (dst / "README.md").exists()
    assert (dst / "20260909T142427Z-calls.log").exists()
    assert (dst / "20260909T142427Z-audit.jsonl").exists()
    assert summary.parallel_calls == 1

    published = "\n".join(p.read_text(encoding="utf-8")
                          for p in dst.iterdir())
    for host in REAL_HOSTS:
        assert host not in published


def test_run_refuses_when_there_is_no_pack_to_redact(tmp_path):
    """Silently writing an empty evidence directory is the worst outcome —
    it looks like evidence."""
    with pytest.raises(SystemExit, match="no evidence packs"):
        run(tmp_path / "empty", tmp_path / "out")
