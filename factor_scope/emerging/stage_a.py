"""Emerging funnel — Stage A: qualify the *industry* before any fund is considered.

The mistake the funnel exists to avoid is jumping straight from a hot theme to a ticker. Stage A is
industry research: a sequence of hard gates over **descriptive** theme inputs — not a fitted score.
A theme advances only if it clears *all five* gates, in order:

1. **Signal strength** — acceleration (the most important sub-signal) + breadth across distinct
   sources, minus a crowding penalty. Acceleration must clear its own floor and the net must clear
   the signal floor.
2. **Late-stage veto** — the anti-hype guardrail (Ben-David: specialized funds launch at the
   attention peak on overvalued underlyings). Acceleration that only *just* cleared its floor while
   the crowd is already in means the wave is cresting, not building — a late signal, vetoed
   outright rather than down-weighted.
3. **Durability** (the decisive filter) — broad adoption *and* a credible path to profitability
   *and* resilience to being a one-cycle fad. The 2026 thematic literature is blunt: the themes that
   lasted shared these; the fads that died (SPAC/cannabis/pandemic plays) lacked them.
4. **Lead-chain corroboration** — does the US relay confirm real end-demand, not a domestic
   narrative alone?
5. **Investable wrapper** — is there a fund/ETF to express it? If not, it is a watch-only idea.

The cut-points are constants chosen for economic meaning, never tuned to returns — they mirror
the band thresholds. The first failing gate is reported so every stop is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ACCEL_ESTABLISHED",
    "ACCEL_MIN",
    "BASE_MAX",
    "BREADTH_REF",
    "CROWD_VETO",
    "SIGNAL_MIN",
    "StageAResult",
    "Theme",
    "qualify_theme",
    "signal_strength",
]

# Economic-meaning constants (never tuned to P&L):
ACCEL_MIN = 0.4  # acceleration is "the most important" sub-signal — it must clear this floor
BREADTH_REF = 6  # distinct corroborating sources/companies that count as full breadth
SIGNAL_MIN = 0.5  # the net signal (accel + breadth − crowding) floor to be more than noise
BASE_MAX = 0.7  # absolute-attention ceiling: above it the theme is *late* (launch-at-peak trap)
CROWD_VETO = 0.7  # crowding at/above this means the crowd is already in — crash-risk territory
ACCEL_ESTABLISHED = 0.6  # below this acceleration has only *just* cleared its floor (cresting)


@dataclass(frozen=True)
class Theme:
    """A candidate industry/theme and the descriptive inputs Stage A reads (point-in-time)."""

    name: str
    acceleration: float  # rate of change of attention/research volume (0..1); higher = accelerating
    base_level: float  # current absolute attention level (0..1); a low base leaves room to run
    breadth: int  # count of distinct sources/companies corroborating the signal
    crowding: float  # crowding penalty (0..1); a crowded theme is a crash-risk gauge
    broad_adoption: bool  # affects many sectors, not a niche
    path_to_profit: bool  # a credible route to earnings, not just a growth story
    fad_resistant: bool  # resilient to being a one-cycle fad
    lead_chain: bool  # the US relay corroborates real end-demand
    wrapper_exists: bool  # an investable fund/ETF (or a fund heavily exposed) exists
    as_of: str  # the research date this read was true as of (point-in-time)
    constituents: tuple[str, ...] = ()  # the theme's reference names — the mapping's overlap seed


@dataclass(frozen=True)
class StageAResult:
    """The verdict on one theme: did it clear, and if not, which gate stopped it (auditable)."""

    theme: str
    passed: bool
    signal_strength: float
    failed_test: str | None  # None when passed; else "signal" | "late_stage" | "durability" | …
    reasons: tuple[str, ...]


def signal_strength(theme: Theme) -> float:
    """Acceleration + breadth (capped against the reference) − the crowding penalty."""

    breadth_norm = min(1.0, theme.breadth / BREADTH_REF)
    return theme.acceleration + breadth_norm - theme.crowding


def qualify_theme(theme: Theme) -> StageAResult:
    """Run the five Stage-A gates in order; stop at the first failure (auditable reason)."""

    sig = signal_strength(theme)
    reasons: list[str] = []

    # 1 — signal strength (acceleration floor + base-level ceiling + net-signal floor).
    if theme.acceleration < ACCEL_MIN:
        reasons.append(f"acceleration {theme.acceleration:.2f} below floor {ACCEL_MIN:.2f}")
        return StageAResult(theme.name, False, sig, "signal", tuple(reasons))
    if theme.base_level > BASE_MAX:
        reasons.append(
            f"base {theme.base_level:.2f} above ceiling {BASE_MAX:.2f} (late: no room to run)"
        )
        return StageAResult(theme.name, False, sig, "signal", tuple(reasons))
    if sig < SIGNAL_MIN:
        reasons.append(f"signal {sig:.2f} below floor {SIGNAL_MIN:.2f} (crowding eats the read)")
        return StageAResult(theme.name, False, sig, "signal", tuple(reasons))
    reasons.append(
        f"signal {sig:.2f} ok (accel {theme.acceleration:.2f}, base {theme.base_level:.2f})"
    )

    # 2 — late-stage veto (the anti-hype guardrail): the crowd is in but acceleration only just
    # cleared its floor → the attention wave is cresting, not building. Veto, never down-weight.
    if theme.crowding >= CROWD_VETO and theme.acceleration < ACCEL_ESTABLISHED:
        reasons.append(
            f"late-stage: crowding {theme.crowding:.2f} at/above veto {CROWD_VETO:.2f} with "
            f"acceleration {theme.acceleration:.2f} barely past its floor "
            f"(below {ACCEL_ESTABLISHED:.2f}) — the wave is cresting, not building"
        )
        return StageAResult(theme.name, False, sig, "late_stage", tuple(reasons))
    reasons.append("not late-stage (crowding and acceleration consistent with a building wave)")

    # 3 — durability (the decisive filter: all three traits must hold).
    if not (theme.broad_adoption and theme.path_to_profit and theme.fad_resistant):
        missing = [
            label
            for label, ok in (
                ("broad-adoption", theme.broad_adoption),
                ("path-to-profit", theme.path_to_profit),
                ("fad-resistance", theme.fad_resistant),
            )
            if not ok
        ]
        reasons.append("not durable: missing " + ", ".join(missing))
        return StageAResult(theme.name, False, sig, "durability", tuple(reasons))
    reasons.append("durable (broad adoption + path to profit + fad-resistant)")

    # 4 — lead-chain corroboration (the US relay confirms real end-demand).
    if not theme.lead_chain:
        reasons.append("no lead-chain corroboration (domestic narrative alone)")
        return StageAResult(theme.name, False, sig, "lead_chain", tuple(reasons))
    reasons.append("lead-chain corroborated")

    # 5 — an investable wrapper exists (else it is a watch-only idea).
    if not theme.wrapper_exists:
        reasons.append("no investable wrapper (watch-only)")
        return StageAResult(theme.name, False, sig, "wrapper", tuple(reasons))
    reasons.append("investable wrapper exists")

    return StageAResult(theme.name, True, sig, None, tuple(reasons))
