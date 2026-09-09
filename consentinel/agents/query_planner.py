"""WU-06 — QueryPlanner. Turns a performer into a multilingual search plan.

FR-2.1 (name × aliases × modality × locale) and FR-2.2 (≥5 locales spanning ≥4
languages). An ADK `LlmAgent` with `output_schema` set and temperature 0, so
the model has no tools, no transfer and no free-form output channel — it fills
in a schema or it fails validation.

The plan is a list of **batches**, not a flat list of queries, because that is
the shape Parallel's Search API takes: one `objective` plus 2-3 keyword queries
of 3-6 words each (WU-04, verified again in WU-05). A batch maps 1:1 onto one
`parallel_search` call.

Two things worth knowing before changing this file:

**Queries are written in the target language.** A Portuguese listing is
invisible to an English query, so `pt-BR` gets *"clone de voz IA"*, not
*"voice clone"* with a locale tag. `MODALITY_TERMS` holds that vocabulary; it
is fed to the model as suggested wording and used by the deterministic builder.

**The planner degrades to a deterministic plan rather than to nothing.** If the
model is down, or returns prose, or keeps failing validation, we still sweep —
using `deterministic_plan`, with `SearchPlan.degraded` set so the UI and the
audit trail can say the plan was not model-written. Fail toward doubt (hard rule
10) means a worse sweep, never a silently skipped one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from consentinel.harness import (
    FailState,
    Harness,
    HarnessDeps,
    HarnessPolicy,
    ValidationError,
)
from consentinel.harness.result import HarnessResult
from consentinel.store.base import Consent, Locale, Modality, Performer

AGENT_NAME = "QueryPlanner"

CANDIDATE_BUDGET = 25
"""Per-sweep candidate cap (WU-07). The plan is sized so a full sweep cannot
blow past it — a budget enforced only at collection time is a budget that gets
spent before anyone notices."""

MAX_BATCHES = 8
"""8 batches × 3 results each = 24, just inside the budget, and enough for 5
locales plus a second modality on the three that matter most."""

MIN_LOCALES = 5
MIN_LANGUAGES = 4

DEFAULT_MODEL = os.environ.get("CONSENTINEL_PLANNER_MODEL", "gemini-2.5-flash")
CACHE_TTL_S = 24 * 3600

log = logging.getLogger("consentinel.query_planner")


# --------------------------------------------------------------------------
# Vocabulary. The whole point of FR-2.2 lives in this table.
# --------------------------------------------------------------------------

DEFAULT_LOCALES: tuple[Locale, ...] = (
    Locale(language="en", region="US"),
    Locale(language="pt", region="BR"),
    Locale(language="es", region="MX"),
    Locale(language="ja", region="JP"),
    Locale(language="hi", region="IN"),
)
"""The five suggested in WU-06: five locales, five languages, four scripts."""

MODALITY_TERMS: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "voice": ("AI voice clone", "synthetic voice", "voice model"),
        "face": ("AI avatar", "deepfake ad", "face swap"),
        "performance": ("AI actor", "digital double"),
    },
    "pt": {
        "voice": ("clone de voz IA", "voz sintética", "modelo de voz"),
        "face": ("avatar IA", "anúncio deepfake", "troca de rosto"),
        "performance": ("ator IA", "dublê digital"),
    },
    "es": {
        "voice": ("clon de voz IA", "voz sintética", "modelo de voz"),
        "face": ("avatar IA", "anuncio deepfake", "cambio de rostro"),
        "performance": ("actor IA", "doble digital"),
    },
    "ja": {
        "voice": ("AI音声クローン", "合成音声", "音声モデル"),
        "face": ("AIアバター", "ディープフェイク広告", "顔交換"),
        "performance": ("AI俳優", "デジタルダブル"),
    },
    "hi": {
        "voice": ("एआई आवाज क्लोन", "सिंथेटिक आवाज", "आवाज मॉडल"),
        "face": ("एआई अवतार", "डीपफेक विज्ञापन", "चेहरा बदलना"),
        "performance": ("एआई अभिनेता", "डिजिटल डबल"),
    },
}

TERRITORY_LANGUAGE = {
    "US": "en", "GB": "en", "CA": "en", "AU": "en", "IE": "en", "NZ": "en",
    "BR": "pt", "PT": "pt",
    "MX": "es", "ES": "es", "AR": "es", "CO": "es", "CL": "es",
    "JP": "ja",
    "IN": "hi",
}
"""Territory → the language we can actually search in. A grant territory whose
language is absent from `MODALITY_TERMS` is skipped rather than searched in
English: an English query aimed at a French market finds nothing and looks like
coverage."""

_LATIN_LANGUAGES = frozenset({"en", "pt", "es", "it", "fr", "de", "nl"})

_ENGLISH_GIVEAWAYS = (
    "voice clone", "ai voice", "synthetic voice", "voice model", "deepfake ad",
    "ai avatar", "face swap", "digital double", "ai actor",
)
"""If one of these shows up in a non-English batch, the model tagged an English
query with a locale instead of translating it. That is the exact failure WU-06
calls out, so it is a validation error, not a warning."""


def word_bounds(language: str) -> tuple[int, int]:
    """3-6 words, except for languages that do not put spaces between them.

    "Mira Vance AI音声クローン" is three whitespace tokens and exactly the right
    query; demanding three more would force padding.
    """
    return (2, 6) if language in ("ja", "zh", "ko", "th") else (3, 6)


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchBatch:
    """One `parallel_search` call: an objective plus 2-3 keyword queries."""

    objective: str
    search_queries: tuple[str, ...]
    locale: Locale
    modality: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "search_queries": list(self.search_queries),
            "locale": str(self.locale),
            "modality": self.modality,
        }


@dataclass(frozen=True)
class SearchPlan:
    performer_id: str
    batches: tuple[SearchBatch, ...]
    model: Optional[str] = None
    degraded: bool = False
    reason: Optional[str] = None

    @property
    def locales(self) -> tuple[Locale, ...]:
        seen: list[Locale] = []
        for b in self.batches:
            if b.locale not in seen:
                seen.append(b.locale)
        return tuple(seen)

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(b.locale.language for b in self.batches))

    @property
    def results_per_batch(self) -> int:
        """Splits the 25-candidate budget across the batches."""
        if not self.batches:
            return 0
        return max(1, CANDIDATE_BUDGET // len(self.batches))

    def as_dict(self) -> dict[str, Any]:
        return {
            "performer_id": self.performer_id,
            "model": self.model,
            "degraded": self.degraded,
            "reason": self.reason,
            "results_per_batch": self.results_per_batch,
            "batches": [b.as_dict() for b in self.batches],
        }


# --------------------------------------------------------------------------
# What the model is allowed to say
# --------------------------------------------------------------------------

class BatchOut(BaseModel):
    """Schema-constrained output. This is the model's entire vocabulary."""

    objective: str = Field(description="One self-contained sentence of intent.")
    search_queries: list[str] = Field(
        description="2-3 keyword queries, 3-6 words each, in the target language.")
    language: str = Field(description="ISO 639-1, lowercase.")
    region: str = Field(description="ISO 3166-1 alpha-2, uppercase.")
    modality: str = Field(description="voice, face or performance.")


class PlanOut(BaseModel):
    batches: list[BatchOut]


# --------------------------------------------------------------------------
# Deterministic construction — also the fallback
# --------------------------------------------------------------------------

def _subjects(performer: Performer) -> list[str]:
    """The name and every alias, shortened to two words.

    A three-word name plus a three-word term is seven words, over the limit, and
    the middle name is rarely what a listing uses.
    """
    out: list[str] = []
    for raw in [performer.name, *performer.aliases]:
        name = " ".join(str(raw).split())
        if not name:
            continue
        parts = name.split()
        short = f"{parts[0]} {parts[-1]}" if len(parts) > 2 else name
        if short not in out:
            out.append(short)
    return out


def _compose_queries(subjects: Sequence[str], terms: Sequence[str],
                     language: str) -> tuple[str, ...]:
    """Cross subjects with terms, keeping only queries inside the word bounds.

    Interleaved rather than subject-major, so three queries reach
    name×term1, name×term2, alias×term1 instead of spending all three on the
    primary name. FR-2.1 says name *and* aliases; a cap of three per batch means
    the ordering decides whether the aliases are ever searched at all.
    """
    lo, hi = word_bounds(language)
    pairs = sorted(
        ((s, t) for s in range(len(subjects)) for t in range(len(terms))),
        key=lambda st: (st[0] + st[1], st[0]),
    )
    queries: list[str] = []
    for s, t in pairs:
        q = f"{subjects[s]} {terms[t]}"
        if lo <= len(q.split()) <= hi and q not in queries:
            queries.append(q)
        if len(queries) == 3:
            break
    return tuple(queries)


def _objective(performer: Performer, locale: Locale, modality: str) -> str:
    aliases = f" (also credited as {', '.join(performer.aliases)})" if performer.aliases else ""
    return (
        f"Find pages that offer, advertise or sell an AI-generated {modality} "
        f"copy of the performer {performer.name}{aliases} — marketplace "
        f"listings, ads and demo pages — published for the "
        f"{locale.language}-speaking market in {locale.region}."
    )


def _planned_locales(performer: Performer, consents: Sequence[Consent],
                     locales: Sequence[Locale]) -> list[Locale]:
    """The default locales, plus any grant territory we have vocabulary for.

    Grants earn coverage because a licence is exactly where out-of-scope use
    hides: permission for one territory, product shipped in another.
    """
    planned = list(locales)
    for consent in consents:
        for territory in consent.territories:
            code = str(territory).upper()
            language = TERRITORY_LANGUAGE.get(code)
            if language is None or language not in MODALITY_TERMS:
                if code not in ("WORLDWIDE",):
                    log.info("%s: no search vocabulary for territory %s; skipping",
                             AGENT_NAME, code)
                continue
            candidate = Locale(language=language, region=code)
            if candidate not in planned:
                planned.append(candidate)
    return planned


def deterministic_plan(
    performer: Performer,
    consents: Sequence[Consent] = (),
    locales: Sequence[Locale] = DEFAULT_LOCALES,
    modalities: Sequence[str] = (Modality.VOICE.value, Modality.FACE.value),
    max_batches: int = MAX_BATCHES,
    *,
    degraded: bool = False,
    reason: Optional[str] = None,
) -> SearchPlan:
    """Build a plan from the vocabulary table. No model call.

    Modality-major: every locale gets the first modality before any locale gets
    the second, so truncating at `max_batches` costs a modality, never a
    language. FR-2.2 is a floor on languages, and it must survive the cap.
    """
    subjects = _subjects(performer)
    batches: list[SearchBatch] = []
    for modality in modalities:
        for locale in _planned_locales(performer, consents, locales):
            if len(batches) >= max_batches:
                break
            terms = MODALITY_TERMS.get(locale.language, {}).get(modality)
            if not terms:
                continue
            queries = _compose_queries(subjects, terms, locale.language)
            if len(queries) < 2:
                log.warning("%s: could not compose 2 queries for %s/%s",
                            AGENT_NAME, locale, modality)
                continue
            batches.append(SearchBatch(
                objective=_objective(performer, locale, modality),
                search_queries=queries, locale=locale, modality=modality,
            ))
    return SearchPlan(performer_id=performer.id, batches=tuple(batches),
                      model=None, degraded=degraded, reason=reason)


# --------------------------------------------------------------------------
# Validation — the guardrail the model is held to
# --------------------------------------------------------------------------

def validate_plan(plan: SearchPlan) -> None:
    """Raise `ValidationError` (semantic: one repair, then fail safe) if the
    plan violates the acceptance conditions of WU-06 or FR-2.2."""
    if not plan.batches:
        raise ValidationError(f"{AGENT_NAME}: the plan is empty")
    if len(plan.batches) > MAX_BATCHES:
        raise ValidationError(
            f"{AGENT_NAME}: {len(plan.batches)} batches exceeds the "
            f"{MAX_BATCHES}-batch cap that keeps a sweep inside "
            f"{CANDIDATE_BUDGET} candidates")
    # Batch shape before coverage floors, so the repair hint names the defect
    # the model can actually fix in one edit.
    for batch in plan.batches:
        if not 2 <= len(batch.search_queries) <= 3:
            raise ValidationError(
                f"{AGENT_NAME}: batch {batch.locale} has "
                f"{len(batch.search_queries)} queries; Parallel takes 2-3")
        if not batch.objective.strip():
            raise ValidationError(f"{AGENT_NAME}: batch {batch.locale} has no objective")
        lo, hi = word_bounds(batch.locale.language)
        for q in batch.search_queries:
            words = len(q.split())
            if not lo <= words <= hi:
                raise ValidationError(
                    f"{AGENT_NAME}: query {q!r} is {words} words; "
                    f"{batch.locale.language} queries must be {lo}-{hi}")
        _check_language(batch)

    if len(plan.locales) < MIN_LOCALES:
        raise ValidationError(
            f"{AGENT_NAME}: {len(plan.locales)} locales, need at least "
            f"{MIN_LOCALES} (FR-2.2)")
    if len(plan.languages) < MIN_LANGUAGES:
        raise ValidationError(
            f"{AGENT_NAME}: {len(plan.languages)} languages "
            f"({', '.join(plan.languages)}), need at least {MIN_LANGUAGES} (FR-2.2)")


def _check_language(batch: SearchBatch) -> None:
    """Catch English text wearing a locale tag.

    Two structural checks, because we cannot detect language properly here: a
    non-Latin-script language must actually produce non-ASCII characters, and no
    non-English batch may contain an English modality term.
    """
    language = batch.locale.language
    if language == "en":
        return
    if language not in _LATIN_LANGUAGES:
        if not any(any(ord(ch) > 127 for ch in q) for q in batch.search_queries):
            raise ValidationError(
                f"{AGENT_NAME}: {language} queries {list(batch.search_queries)} are "
                f"pure ASCII — write them in {language}, not English with a locale tag")
    for q in batch.search_queries:
        lowered = q.lower()
        for giveaway in _ENGLISH_GIVEAWAYS:
            if giveaway in lowered:
                raise ValidationError(
                    f"{AGENT_NAME}: {language} query {q!r} contains the English "
                    f"term {giveaway!r}; translate it")


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

Generate = Callable[[str, str], str]
"""(instruction, payload) -> raw model text. Injected so the plan logic is
testable without a Vertex call."""


@dataclass
class QueryPlanner:
    deps: HarnessDeps = field(default_factory=HarnessDeps)
    generate: Optional[Generate] = None
    model: str = DEFAULT_MODEL
    locales: Sequence[Locale] = DEFAULT_LOCALES
    modalities: Sequence[str] = (Modality.VOICE.value, Modality.FACE.value)
    max_batches: int = MAX_BATCHES
    timeout_s: float = 60.0
    max_attempts: int = 2
    cache_ttl_s: int = CACHE_TTL_S
    sleep: Callable[[float], None] = time.sleep
    _harness: Optional[Harness] = field(default=None, repr=False, compare=False)

    @property
    def policy(self) -> HarnessPolicy:
        return HarnessPolicy(
            agent_name=AGENT_NAME,
            fail_state=FailState.DEGRADED,
            timeout_s=self.timeout_s,
            max_attempts=self.max_attempts,
            max_repairs=1,
            tools=(),                      # a planner needs no capability
            output_schema=PlanOut,
            temperature=0.0,               # WU-06: temperature 0
            cache="ttl",
            cache_ttl_s=self.cache_ttl_s,
        )

    @property
    def harness(self) -> Harness:
        if self._harness is None:
            self._harness = Harness(self.policy, self.deps, sleep=self.sleep)
        return self._harness

    def _generator(self) -> Generate:
        if self.generate is None:
            self.generate = adk_generator(self.model)
        return self.generate

    # ------------------------------------------------------------------

    def plan(self, performer: Performer,
             consents: Sequence[Consent] = ()) -> SearchPlan:
        """Never raises. A model failure returns the deterministic plan with
        `degraded=True` rather than nothing."""
        result = self.plan_detailed(performer, consents)
        if result.ok:
            return result.value
        fallback = deterministic_plan(
            performer, consents, self.locales, self.modalities, self.max_batches,
            degraded=True, reason=result.reason,
        )
        log.warning("%s: falling back to the deterministic plan (%s): "
                    "%d batches, %d locales, %d languages",
                    AGENT_NAME, result.reason, len(fallback.batches),
                    len(fallback.locales), len(fallback.languages))
        return fallback

    def plan_detailed(self, performer: Performer,
                      consents: Sequence[Consent] = ()) -> HarnessResult:
        instruction = build_instruction(self.locales, self.modalities,
                                        self.max_batches)
        payload = build_payload(performer, consents, self.locales, self.modalities)
        cache_key = _cache_key(payload, instruction, self.deps.prompt_version,
                               self.model)

        def invoke(repair_hint: Optional[str]) -> SearchPlan:
            prompt = payload
            if repair_hint:
                # One repair, and it carries the validator's own words. See
                # DESIGN.md Part I section 3.
                prompt = (
                    f"{payload}\n\nYour previous answer was rejected: "
                    f"{repair_hint}\nReturn a corrected plan."
                )
            raw = self._generator()(instruction, prompt)
            return parse_plan(raw, performer.id, self.model)

        result = self.harness.run(
            invoke, subject_id=performer.id, cache_key=cache_key,
            validators=(validate_plan,), provider="gemini",
        )
        self._audit_plan(result, performer)
        return result

    def _audit_plan(self, result: HarnessResult, performer: Performer) -> None:
        plan: Optional[SearchPlan] = result.value if result.ok else None
        self.deps.audit.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": AGENT_NAME,
            "event": "plan",
            "subject_id": performer.id,
            "ok": result.ok,
            "fail_state": result.fail_state.value if result.fail_state else None,
            "reason": result.reason,
            "model": self.model if not result.from_cache else None,
            "prompt_version": result.prompt_version,
            "temperature": 0.0,
            "batches": len(plan.batches) if plan else 0,
            "locales": [str(loc) for loc in plan.locales] if plan else [],
            "languages": list(plan.languages) if plan else [],
            "from_cache": result.from_cache,
            "cache_age_s": result.cache_age_s,
            "attempts": result.attempts,
            "repairs": result.repairs,
            "latency_s": round(result.duration_s, 4),
        })


# --------------------------------------------------------------------------
# Prompt, parsing, cache key
# --------------------------------------------------------------------------

def build_instruction(locales: Sequence[Locale] = DEFAULT_LOCALES,
                      modalities: Sequence[str] = (Modality.VOICE.value,
                                                   Modality.FACE.value),
                      max_batches: int = MAX_BATCHES) -> str:
    """The system instruction. Constraints first — a model that reads only the
    first paragraph should still produce the right shape."""
    vocabulary = []
    for locale in locales:
        for modality in modalities:
            terms = MODALITY_TERMS.get(locale.language, {}).get(modality)
            if terms:
                vocabulary.append(
                    f"  {locale} {modality}: " + " · ".join(terms))
    return f"""You plan web searches that look for unauthorised AI copies of a performer's
voice or face. You return a search plan and nothing else. No prose, no
explanation, no markdown.

HARD CONSTRAINTS
- Each batch holds 2 or 3 keyword queries. Never 1, never 4.
- Each query is 3 to 6 words. Japanese, Chinese, Korean and Thai queries may be
  2 to 6 whitespace-separated words. "Mira Vance AI voice clone" is the right
  shape; a sentence is not.
- Write every query IN the language of its batch's locale. Do not write English
  queries and label them with a foreign locale — a Portuguese listing is
  invisible to an English query.
- Cover at least {MIN_LOCALES} locales spanning at least {MIN_LANGUAGES} languages.
- At most {max_batches} batches in total.
- Cross the performer's name and each alias with the modality terms.
- `objective` is one self-contained sentence naming the performer, the modality
  and the market. It steers ranking; it is not a query.

SUGGESTED VOCABULARY (use it, or better wording in the same language)
{chr(10).join(vocabulary)}

Return JSON matching the schema: a list of batches, each with objective,
search_queries, language, region, modality."""


def build_payload(performer: Performer, consents: Sequence[Consent] = (),
                  locales: Sequence[Locale] = DEFAULT_LOCALES,
                  modalities: Sequence[str] = (Modality.VOICE.value,
                                               Modality.FACE.value)) -> str:
    """The user turn: the performer, their aliases, and their existing grants.

    Grants are context, not an exclusion list. A licensed use still gets swept
    and reported — whether a match is authorised is the reconciler's decision,
    from the registry, never the planner's (hard rule 4).
    """
    return json.dumps({
        "performer": {
            "id": performer.id,
            "name": performer.name,
            "aliases": list(performer.aliases),
        },
        "existing_grants": [
            {
                "licensee": c.licensee,
                "permitted_uses": [
                    u.value if hasattr(u, "value") else str(u)
                    for u in c.permitted_uses
                ],
                "territories": list(c.territories),
            }
            for c in consents
        ],
        "requested_locales": [
            {"language": loc.language, "region": loc.region}
            for loc in _planned_locales(performer, consents, locales)
        ],
        "modalities": list(modalities),
        "note": ("Grants are context only. Plan coverage for granted "
                 "territories too: permission in one territory and shipment in "
                 "another is exactly what we are looking for."),
    }, ensure_ascii=False, sort_keys=True)


def parse_plan(raw: str, performer_id: str, model: Optional[str]) -> SearchPlan:
    """Model text -> `SearchPlan`. Anything unparseable is a semantic failure,
    so it gets one repair rather than a retry."""
    text = (raw or "").strip()
    if text.startswith("```"):
        # Some models fence JSON even when told not to. Cheap to accept.
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}") + 1]
    if not text:
        raise ValidationError(f"{AGENT_NAME}: the model returned nothing")
    try:
        parsed = PlanOut.model_validate_json(text)
    except PydanticValidationError as exc:
        raise ValidationError(f"{AGENT_NAME}: output did not match the schema: {exc}")
    except ValueError as exc:
        raise ValidationError(f"{AGENT_NAME}: output was not JSON: {exc}")

    batches = tuple(
        SearchBatch(
            objective=b.objective.strip(),
            search_queries=tuple(q.strip() for q in b.search_queries if q.strip()),
            locale=Locale(language=b.language.strip().lower(),
                          region=b.region.strip().upper()),
            modality=b.modality.strip().lower(),
        )
        for b in parsed.batches
    )
    return SearchPlan(performer_id=performer_id, batches=batches, model=model)


def _cache_key(payload: str, instruction: str, prompt_version: str,
               model: str) -> str:
    """Includes `prompt_version` and the instruction text, per hard rule 8: a
    model call keyed without them serves yesterday's prompt forever."""
    digest = hashlib.sha256(
        json.dumps({"payload": payload, "instruction": instruction,
                    "prompt_version": prompt_version, "model": model},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"query_planner:{prompt_version}:{digest}"


# --------------------------------------------------------------------------
# ADK wiring
# --------------------------------------------------------------------------

def build_agent(model: str = DEFAULT_MODEL, *,
                instruction: Optional[str] = None) -> Any:
    """The ADK `LlmAgent`. Schema-constrained, temperature 0, no tools.

    `output_schema` is what removes the free-form channel: with it set, ADK
    refuses tools and transfer, so this agent can only fill in `PlanOut`.
    """
    from google.adk.agents import LlmAgent  # noqa: PLC0415 - lazy: importing ADK is slow
    from google.genai import types  # noqa: PLC0415

    return LlmAgent(
        name=AGENT_NAME,
        model=model,
        instruction=instruction or build_instruction(),
        output_schema=PlanOut,
        output_key="search_plan",
        include_contents="none",          # single shot; no conversation to carry
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
    )


def adk_generator(model: str = DEFAULT_MODEL) -> Generate:
    """Run the ADK agent once and return its final text."""
    from google.genai import types  # noqa: PLC0415

    def generate(instruction: str, payload: str) -> str:
        from google.adk.runners import InMemoryRunner  # noqa: PLC0415

        agent = build_agent(model, instruction=instruction)
        runner = InMemoryRunner(agent=agent, app_name="consentinel")
        session = _sync(runner.session_service.create_session(
            app_name="consentinel", user_id=AGENT_NAME))
        text = ""
        for event in runner.run(
            user_id=AGENT_NAME, session_id=session.id,
            new_message=types.Content(role="user",
                                      parts=[types.Part(text=payload)]),
        ):
            for part in (event.content.parts if event.content else []) or []:
                if getattr(part, "text", None):
                    text = part.text
        return text

    return generate


def _sync(coro: Any) -> Any:
    """Run one coroutine from sync code, whether or not a loop is running.

    The web layer is async; this module is called from both sides.
    """
    import asyncio  # noqa: PLC0415

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures  # noqa: PLC0415

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def summarise(plan: SearchPlan) -> str:
    """One readable line per plan, for the log and the demo."""
    return (f"{AGENT_NAME} performer={plan.performer_id} "
            f"batches={len(plan.batches)} locales={len(plan.locales)} "
            f"languages={','.join(plan.languages)} "
            f"results_per_batch={plan.results_per_batch} "
            f"degraded={plan.degraded}")


def iter_search_calls(plan: SearchPlan) -> Iterable[dict[str, Any]]:
    """The plan as `parallel_search(**kwargs)` calls, ready for WU-07."""
    for batch in plan.batches:
        yield {
            "objective": batch.objective,
            "search_queries": list(batch.search_queries),
            "locale": batch.locale,
            "max_results": plan.results_per_batch,
        }
