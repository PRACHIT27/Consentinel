"""WU-11 — the injection canary. Label the attempt; never obey it.

`DESIGN.md` §4.3. A cheap regex pass over fetched page text: does this page
contain imperative language addressed at an AI system?

**This is not the defence.** The defence is structural and already in place —
the reconciler never sees page text (hard rule 2), the extractor has no tools
and no free-form channel (hard rule 3), and a quote must be a real substring
(hard rule 5). An injected instruction has nowhere to land. What this module
adds is *visibility*: the finding carries a badge saying what the page tried,
which is worth thirty seconds of demo and is the difference between "we are
safe" and "we can show you we are safe".

So it never blocks. A page trying to manipulate us is frequently the very page
that is infringing, and suppressing it would throw away the finding we went
looking for.

**It records marker names, not page text.** DESIGN §7: never log page content
verbatim — it is attacker-controlled and may carry personal data. The spans are
offsets into the text we already hold, so the UI can highlight the sentence it
is already rendering (escaped, per §4.4) without the audit trail becoming a
copy of the attack.

False positives cost a badge, not a finding. That asymmetry is why the patterns
below lean towards catching things: `you are now a…` on a shopping page is a
harmless mislabel, while a missed injection is a story about a system that
obeyed a web page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Pattern

MAX_SCAN_CHARS = 200_000
"""Bounded work on unbounded input. `fetch_page` already caps at 2 MB and
Triage at 20,000 characters, so this only matters for direct callers."""

REASONING_MARKER = "injection_suspected"
"""`Finding` has no boolean for this in the frozen contract, so the flag rides
in `reasoning` behind a stable prefix — `[injection_suspected: names]` — and
`reasoning_flags()` parses it back out. See the VERSION.md entry: a proper
field is a one-line contract change that needs the team's agreement, not mine
alone."""


PATTERNS: dict[str, tuple[str, ...]] = {
    "instruction_override": (
        r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier|preceding)\s+"
        r"(?:instructions?|prompts?|rules?|directions?)",
        r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\b",
        r"forget\s+(?:everything|all)\s+(?:above|before|you)",
        r"override\s+(?:your\s+)?(?:previous\s+)?(?:instructions?|system\s+prompt)",
    ),
    "role_assignment": (
        r"you\s+are\s+now\s+(?:a|an|the|my)\b",
        r"from\s+now\s+on[, ]+you\b",
        r"(?:act|behave)\s+as\s+(?:a|an|the|if\s+you)\b",
        r"pretend\s+(?:to\s+be|you\s+are)\b",
        r"your\s+new\s+(?:role|task|instructions?)\s+is\b",
    ),
    "verdict_steering": (
        r"mark\s+(?:this|it|the\s+\w+)\s+as\s+(?:authori[sz]ed|licen[sc]ed|safe|cleared|approved)",
        r"report\s+(?:this|it)\s+as\s+(?:authori[sz]ed|licen[sc]ed|compliant)",
        r"this\s+(?:use|content|page|listing)\s+is\s+(?:fully\s+)?(?:authori[sz]ed|licen[sc]ed)",
        r"set\s+(?:is_synthetic_claim|is_commercial|confidence|verdict|depicts_named_person)\b",
        r"do\s+not\s+(?:flag|report|record)\s+(?:this|it)\b",
        r"(?:skip|bypass)\s+(?:the\s+)?(?:review|verification|check)\b",
    ),
    "role_marker": (
        # `(?m)` so `^` means "start of a line", not "start of the page":
        # "Our system: reliability you can trust" mid-sentence is not an
        # injection, but a line that begins `system:` is pretending to be one.
        r"(?m)^\s{0,4}(?:system|assistant|user|developer)\s*:",
        r"<\|(?:im_start|im_end|system|endoftext)\|>",
        r"(?m)^\s{0,4}#{2,}\s*(?:system|instruction|prompt)\b",
        r"\[\s*(?:system|instruction)\s*\]",
    ),
    "prompt_exfiltration": (
        r"(?:reveal|show|print|repeat|output)\s+(?:me\s+)?(?:your\s+)?"
        r"(?:system\s+)?(?:prompt|instructions?|rules)",
        r"repeat\s+(?:the\s+)?(?:text|words|everything)\s+above",
    ),
    "tool_coercion": (
        r"(?:call|invoke|use)\s+(?:the\s+)?(?:function|tool|api)\b",
        r"send\s+(?:an\s+)?(?:email|message|request)\s+to\b",
        r"(?:fetch|browse|navigate)\s+to\s+https?://",
    ),
}
"""Grouped by what the page is *trying to do*, because that is what a reviewer
wants on the badge. The names travel; the matched text does not."""

_COMPILED: dict[str, tuple[Pattern[str], ...]] = {
    # IGNORECASE for every pattern, as a flag rather than an inline `(?i)`.
    # Inline flags only apply when they lead the pattern, and half of these
    # start with `(?:...)` — which is how the first version of this file
    # silently ran four families case-sensitively.
    name: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
    for name, patterns in PATTERNS.items()
}


@dataclass(frozen=True)
class InjectionScan:
    """What the page tried, named rather than quoted."""

    suspected: bool = False
    markers: tuple[str, ...] = ()
    hits: int = 0
    spans: tuple[tuple[int, int], ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        return {
            "injection_suspected": self.suspected,
            "injection_markers": list(self.markers),
            "injection_hits": self.hits,
        }

    def badge(self) -> Optional[str]:
        """One short line for the findings screen."""
        if not self.suspected:
            return None
        return (f"Injection attempt detected: {', '.join(self.markers)} "
                f"({self.hits} {'match' if self.hits == 1 else 'matches'}). "
                "Recorded, not followed.")


def scan(text: str) -> InjectionScan:
    """Look for instructions addressed at an AI system. Never raises.

    Returns marker *categories*, a hit count, and spans into `text` for
    highlighting. Deliberately no excerpt: the whole point of §7 is that the
    trail does not become a copy of the attack.
    """
    body = (text or "")[:MAX_SCAN_CHARS]
    if not body.strip():
        return InjectionScan()

    markers: list[str] = []
    spans: list[tuple[int, int]] = []
    hits = 0
    for name, patterns in _COMPILED.items():
        found = False
        for pattern in patterns:
            for match in pattern.finditer(body):
                hits += 1
                found = True
                spans.append((match.start(), match.end()))
        if found:
            markers.append(name)

    if not markers:
        return InjectionScan()
    return InjectionScan(suspected=True, markers=tuple(sorted(markers)),
                         hits=hits, spans=tuple(sorted(spans)))


# --------------------------------------------------------------------------
# Carrying the flag on a Finding, which has no field for it
# --------------------------------------------------------------------------

def annotate_reasoning(reasoning: Optional[str], scan_result: InjectionScan) -> Optional[str]:
    """Prefix `Finding.reasoning` with a parseable marker.

    A workaround, and labelled as one: `Finding` has no `injection_suspected`
    column in the frozen contract, and adding one is a MAJOR change that needs
    all three of us. Until then the flag lives here in a shape a UI can parse
    and a human can read.
    """
    if not scan_result.suspected:
        return reasoning
    prefix = f"[{REASONING_MARKER}: {','.join(scan_result.markers)}]"
    return f"{prefix} {reasoning}".strip() if reasoning else prefix


def reasoning_flags(reasoning: Optional[str]) -> tuple[str, ...]:
    """Marker names back out of `reasoning`, for the badge. Empty if none."""
    if not reasoning:
        return ()
    match = re.match(rf"\[{REASONING_MARKER}:\s*([^\]]+)\]", reasoning.strip())
    if not match:
        return ()
    return tuple(m.strip() for m in match.group(1).split(",") if m.strip())


def is_flagged(reasoning: Optional[str]) -> bool:
    return bool(reasoning_flags(reasoning))


def strip_marker(reasoning: Optional[str]) -> Optional[str]:
    """The human-readable part, with the marker removed."""
    if not reasoning:
        return reasoning
    return re.sub(rf"^\[{REASONING_MARKER}:[^\]]*\]\s*", "", reasoning.strip()) or None


def marker_names() -> Iterable[str]:
    return tuple(PATTERNS)
