"""The harness. Every agent runs through this.

Twelve steps, in order (DESIGN.md Part II section 4):

  1 open span   2 budget   3 cache   4 armor in   5 invoke   6 armor out
  7 validate    8 repair x1   9 retry and circuit break   10 FAIL SAFE
  11 audit      12 metrics and close span

Built once so fourteen agents are cheap. If using it is awkward, people will
bypass it and every guarantee here evaporates — so `run` takes callables and
nothing else.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from consentinel.harness.errors import (
    BudgetExceeded,
    CapabilityError,
    CircuitBreaker,
    CircuitOpen,
    ErrorClass,
    ValidationError,
    backoff_seconds,
    classify,
)
from consentinel.harness.policy import HarnessPolicy
from consentinel.harness.ports import HarnessDeps
from consentinel.harness.result import HarnessResult

Invoke = Callable[[Optional[str]], Any]
"""Performs the actual work. Receives a repair hint on the second attempt, or None."""

Validator = Callable[[Any], None]
"""Raises ValidationError if the output is unacceptable. See DESIGN.md Part I section 3 L3."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Harness:
    def __init__(
        self,
        policy: HarnessPolicy,
        deps: Optional[HarnessDeps] = None,
        breaker: Optional[CircuitBreaker] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy
        self.deps = deps or HarnessDeps()
        self.breaker = breaker or CircuitBreaker()
        self._sleep = sleep

    # ------------------------------------------------------------------
    # Capability boundary
    # ------------------------------------------------------------------

    def guard_tool(self, tool_name: str) -> None:
        """Refuse any tool the policy did not declare.

        This is the trust boundary in code rather than convention. There is no
        override parameter on purpose: an escape hatch here would be the first
        thing reached for at 2am, and the whole guarantee rests on there not
        being one.
        """
        if not self.policy.allows(tool_name):
            raise CapabilityError(
                f"{self.policy.agent_name} may not call {tool_name!r}; "
                f"declared tools: {self.policy.tools or '()'}"
            )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        invoke: Invoke,
        *,
        subject_id: Optional[str] = None,
        cache_key: Optional[str] = None,
        untrusted_text: Optional[str] = None,
        validators: Sequence[Validator] = (),
        response_text: Optional[Callable[[Any], str]] = None,
        provider: Optional[str] = None,
    ) -> HarnessResult:
        p = self.policy
        d = self.deps
        provider = provider or p.agent_name
        started = time.monotonic()

        attrs: dict[str, Any] = {
            "agent": p.agent_name,
            "prompt_version": d.prompt_version,
            "subject_id": subject_id,
        }
        armor_findings: tuple[str, ...] = ()
        injection = False
        attempts = 0
        repairs = 0
        from_cache = False
        cache_age: Optional[float] = None

        # 1 — span
        with d.tracer.span(f"agent.{p.agent_name}", attrs):
            try:
                # 2 — budget: refuse before spending anything
                self.breaker.guard(provider)

                # 3 — cache
                if p.cache != "none" and cache_key is not None:
                    hit = d.cache.get(cache_key)
                    if hit is not None:
                        from_cache = True
                        cache_age = hit.age_seconds()
                        return self._finish(
                            HarnessResult(
                                ok=True, value=hit.value, agent=p.agent_name,
                                prompt_version=d.prompt_version, from_cache=True,
                                cache_age_s=cache_age, attempts=0,
                                duration_s=time.monotonic() - started,
                            ),
                            attrs, subject_id,
                        )

                # 4 — Model Armor on the way in. Inspect-only: we label, we do
                #     not block, because a manipulative page is often the
                #     infringing one and blocking would suppress the finding.
                if p.armor_prompt and untrusted_text:
                    v = d.armor.sanitize_prompt(p.armor_prompt, untrusted_text)
                    armor_findings = v.findings
                    injection = v.injection_suspected
                    for f in v.findings:
                        d.metrics.counter("model_armor.detections", type=f, path="in",
                                          agent=p.agent_name)

                repair_hint: Optional[str] = None
                last_exc: Optional[BaseException] = None

                while attempts <= p.max_attempts:
                    attempts += 1
                    try:
                        if time.monotonic() - started > p.timeout_s:
                            raise BudgetExceeded(
                                f"{p.agent_name}: exceeded {p.timeout_s}s budget"
                            )

                        # 5 — invoke
                        value = invoke(repair_hint)

                        # 6 — Model Armor on the way out. Inspect-and-block: a
                        #     notice must never carry leaked PII or a bad URL.
                        if p.armor_response and response_text is not None:
                            rv = d.armor.sanitize_response(
                                p.armor_response, response_text(value)
                            )
                            for f in rv.findings:
                                d.metrics.counter("model_armor.detections", type=f,
                                                  path="out", agent=p.agent_name)
                            if rv.blocked:
                                raise ValidationError(
                                    "response blocked by Model Armor: "
                                    + ", ".join(rv.findings)
                                )

                        # 7 — validate
                        for check in validators:
                            check(value)

                        self.breaker.record_success(provider)

                        if p.cache != "none" and cache_key is not None:
                            d.cache.put(cache_key, value, p.cache_ttl_s)

                        return self._finish(
                            HarnessResult(
                                ok=True, value=value, agent=p.agent_name,
                                prompt_version=d.prompt_version, attempts=attempts,
                                repairs=repairs, duration_s=time.monotonic() - started,
                                armor_findings=armor_findings,
                                injection_suspected=injection,
                            ),
                            attrs, subject_id,
                        )

                    except BaseException as exc:  # noqa: BLE001 - classified below
                        last_exc = exc
                        kind = classify(exc)

                        if kind is ErrorClass.SEMANTIC:
                            d.metrics.counter(
                                "extraction.validation_failures",
                                reason=type(exc).__name__, agent=p.agent_name,
                            )
                            # 8 — one repair, then stop. A parse failure must
                            #     never be retried into a verdict.
                            if repairs < p.max_repairs:
                                repairs += 1
                                repair_hint = str(exc)
                                attempts -= 1  # a repair is not a transient retry
                                continue
                            break

                        if kind is ErrorClass.PERMANENT:
                            if not isinstance(exc, (CapabilityError, CircuitOpen,
                                                    BudgetExceeded)):
                                self.breaker.record_failure(provider)
                            d.metrics.counter("tool.errors", **{
                                "class": "permanent", "agent": p.agent_name})
                            break

                        # 9 — transient: backoff and retry
                        self.breaker.record_failure(provider)
                        d.metrics.counter("tool.errors", **{
                            "class": "transient", "agent": p.agent_name})
                        if attempts > p.max_attempts:
                            break
                        self._sleep(backoff_seconds(attempts))

                raise last_exc if last_exc else RuntimeError("no attempt was made")

            except BaseException as exc:  # noqa: BLE001
                # 10 — FAIL SAFE. The governing rule of the system: every
                # failure resolves toward doubt, never toward permission.
                # Nothing below can produce `authorized` or `cleared`.
                return self._finish(
                    HarnessResult(
                        ok=False, value=None, fail_state=p.fail_state,
                        reason=f"{type(exc).__name__}: {exc}",
                        agent=p.agent_name, prompt_version=d.prompt_version,
                        from_cache=from_cache, cache_age_s=cache_age,
                        attempts=attempts, repairs=repairs,
                        duration_s=time.monotonic() - started,
                        armor_findings=armor_findings,
                        injection_suspected=injection,
                    ),
                    attrs, subject_id,
                )

    # ------------------------------------------------------------------

    def _finish(self, result: HarnessResult, attrs: dict[str, Any],
                subject_id: Optional[str]) -> HarnessResult:
        """11 — audit, 12 — metrics. Both happen on success and failure alike:
        a gap in the trail must mean we did not run, never that we ran and lost
        the record."""
        d = self.deps
        d.audit.append({
            "ts": _utcnow().isoformat(),
            "actor": result.agent,
            "subject_id": subject_id,
            "ok": result.ok,
            "fail_state": result.fail_state.value if result.fail_state else None,
            "reason": result.reason,
            "prompt_version": result.prompt_version,
            "tool_calls": [{
                "from_cache": result.from_cache,
                "cache_age_s": result.cache_age_s,
                "attempts": result.attempts,
                "repairs": result.repairs,
            }],
            "armor_findings": list(result.armor_findings),
            "injection_suspected": result.injection_suspected,
            "duration_s": round(result.duration_s, 4),
        })
        d.metrics.histogram("agent.duration_seconds", result.duration_s,
                            agent=result.agent)
        d.metrics.counter("agent.runs", agent=result.agent,
                          outcome="ok" if result.ok else (
                              result.fail_state.value if result.fail_state else "error"))
        if result.from_cache:
            d.metrics.counter("cache.hits", agent=result.agent)
        attrs.update({
            "ok": result.ok,
            "cache_hit": result.from_cache,
            "cache_age_s": result.cache_age_s,
            "attempts": result.attempts,
            "retry_count": max(0, result.attempts - 1),
            "injection_suspected": result.injection_suspected,
        })
        return result
