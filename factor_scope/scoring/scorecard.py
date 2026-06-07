"""The rolling self-scoring scorecard — a descriptive mirror, nothing more.

Pure functions over ``(confidence, hit)`` pairs and scored calls: the **Brier** score, the **Brier
skill score** vs the base-rate forecaster, reliability-by-confidence buckets, and per-state-pattern
hit-rates. The whole block is gated on a minimum sample so noise cannot masquerade as signal. The
mirror's *only* sanctioned influence on tomorrow's digest is :func:`confidence_nudge` — it may pull
a stated confidence toward what that confidence bucket actually delivered. It can never change a
factor state, open the trend gate, or supply a number to the artifact (those are tested impossible).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from factor_scope.contract import ReliabilityBucket, Scorecard
from factor_scope.scoring.scorer import Outcome, ScoredCall

DEFAULT_WINDOW = "60d"
DEFAULT_MIN_N = 10  # below this the whole scorecard is gated (sample too thin to read)
DEFAULT_BUCKET_MIN_N = 2  # a reliability bucket needs at least this many calls to show
DEFAULT_PATTERN_MIN_N = 3  # a state-pattern needs at least this many calls to be judged
DEFAULT_TOL = 0.1  # confidence vs realised gap before we call it over/under-confident
DEFAULT_NUDGE = 0.5  # how far confidence_nudge moves toward realised reliability
DEFAULT_DAMPEN = 0.5  # how far a weak-pattern read pulls confidence toward zero


def parse_window_days(window: str) -> int:
    """Days in a rolling-window string like ``"60d"`` — the single source the filter trims by.

    Parsing the same ``window`` that labels the scorecard means the displayed window and the applied
    cutoff can never drift. Only the day unit is supported; anything else is a programming error.
    """

    if not window.endswith("d"):
        raise ValueError(f"unsupported scorecard window: {window!r}")
    return int(window[:-1])


def brier(pairs: list[tuple[float, bool]]) -> float:
    """Mean squared error of stated confidence vs realised outcome (0 best, 1 worst)."""

    return sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs)


def base_rate(outcomes: list[bool]) -> float:
    """The overall hit fraction — what the dumb base-rate forecaster would predict."""

    return sum(1 for y in outcomes if y) / len(outcomes)


def brier_skill_score(pairs: list[tuple[float, bool]]) -> float | None:
    """Skill vs the base-rate forecaster: ``1 - brier/brier_ref``. ``None`` if the base is perfect.

    The reference always predicts the base rate ``b``; its Brier is ``b·(1-b)``. A degenerate base
    rate (every call hit, or every call missed) makes the reference perfect → skill is undefined.
    """

    rate = base_rate([y for _, y in pairs])
    reference = rate * (1.0 - rate)
    if reference == 0.0:
        return None
    return 1.0 - brier(pairs) / reference


def reliability_buckets(
    pairs: list[tuple[float, bool]],
    *,
    bucket_min_n: int = DEFAULT_BUCKET_MIN_N,
    tol: float = DEFAULT_TOL,
) -> list[ReliabilityBucket]:
    """Realised hit-rate per stated-confidence bucket (snapped to the nearest 0.1).

    A bucket with fewer than ``bucket_min_n`` calls is hidden so noise cannot look like a miscall.
    """

    groups: dict[float, list[bool]] = defaultdict(list)
    for conf, hit in pairs:
        groups[round(conf, 1)].append(hit)
    buckets: list[ReliabilityBucket] = []
    for center in sorted(groups):
        outcomes = groups[center]
        if len(outcomes) < bucket_min_n:
            continue
        realised = base_rate(outcomes)
        note: str | None = None
        if realised < center - tol:
            note = "overconfident"
        elif realised > center + tol:
            note = "underconfident"
        buckets.append(ReliabilityBucket(bucket=center, realised=realised, note=note))
    return buckets


def weak_patterns(
    scored: list[ScoredCall],
    *,
    pattern_min_n: int = DEFAULT_PATTERN_MIN_N,
    tol: float = DEFAULT_TOL,
) -> list[str]:
    """State patterns the digest is systematically overconfident on (stated ≫ realised).

    Only patterns with enough calls are judged. Sorted worst-gap first for a stable, useful read.
    """

    groups: dict[str, list[ScoredCall]] = defaultdict(list)
    for sc in scored:
        key = "+".join(sc.call.state_pattern) or "—"
        groups[key].append(sc)
    flagged: list[tuple[float, str]] = []
    for key in groups:
        calls = groups[key]
        if len(calls) < pattern_min_n:
            continue
        hit = base_rate([sc.outcome is Outcome.HIT for sc in calls])
        conf = sum(sc.call.confidence for sc in calls) / len(calls)
        if conf - hit > tol:
            text = f"{key} overconfident (hit {hit:.0%} vs conf {conf:.0%}, n={len(calls)})"
            flagged.append((conf - hit, text))
    return [text for _, text in sorted(flagged, reverse=True)]


def build_scorecard(
    scored: list[ScoredCall],
    as_of: str,
    *,
    window: str = DEFAULT_WINDOW,
    min_n: int = DEFAULT_MIN_N,
    bucket_min_n: int = DEFAULT_BUCKET_MIN_N,
    pattern_min_n: int = DEFAULT_PATTERN_MIN_N,
    tol: float = DEFAULT_TOL,
) -> Scorecard:
    """Roll up scored calls into the descriptive :class:`~factor_scope.contract.Scorecard`.

    The window is *rolling*: calls made before ``as_of - window`` are trimmed first, so an old
    regime can't rule a new one. The cutoff is parsed from the same ``window`` that
    labels the result, so the two can't drift. Abstains make no claim and are excluded. Below
    ``min_n`` decided calls the block is gated: it reports the sample size only and nothing more.
    """

    cutoff = (date.fromisoformat(as_of) - timedelta(days=parse_window_days(window))).isoformat()
    in_window = [sc for sc in scored if sc.call.as_of >= cutoff]
    decided = [sc for sc in in_window if sc.outcome in (Outcome.HIT, Outcome.MISS)]
    n = len(decided)
    if n < min_n:
        return Scorecard(window=window, n=n)
    pairs = [(sc.call.confidence, sc.outcome is Outcome.HIT) for sc in decided]
    skill = brier_skill_score(pairs)
    return Scorecard(
        window=window,
        n=n,
        brier=round(brier(pairs), 4),
        skill_vs_baserate=(f"{skill:+.2f}" if skill is not None else None),
        reliability=reliability_buckets(pairs, bucket_min_n=bucket_min_n, tol=tol),
        weak_patterns=weak_patterns(decided, pattern_min_n=pattern_min_n, tol=tol),
    )


def confidence_nudge(
    scorecard: Scorecard | None, base_confidence: float, *, strength: float = DEFAULT_NUDGE
) -> float:
    """Pull a stated confidence toward its bucket's realised reliability — bounded to ``[0, 1]``.

    This is the mirror's *only* channel into the digest. It moves a number it does not own; it has
    no access to a factor state or the gate and cannot return anything but a confidence in range.
    """

    if scorecard is None or not scorecard.reliability:
        return base_confidence
    center = round(base_confidence, 1)
    match = next((b for b in scorecard.reliability if abs(b.bucket - center) < 1e-9), None)
    if match is None:
        return base_confidence
    nudged = base_confidence + strength * (match.realised - base_confidence)
    return min(1.0, max(0.0, nudged))


def dampen_for_weak_pattern(
    scorecard: Scorecard | None,
    confidence: float,
    pattern: tuple[str, ...],
    *,
    factor: float = DEFAULT_DAMPEN,
) -> float:
    """Lower confidence on a state-pattern the mirror has been overconfident on.

    The second sanctioned confidence channel (alongside :func:`confidence_nudge`): if any token of
    this item's state pattern appears among the scorecard's flagged ``weak_patterns``, pull the
    confidence toward zero. Like the nudge it moves a number it does not own — it can only *lower*
    confidence, never raise it, change an action, a state, or the gate.
    """

    if scorecard is None or not scorecard.weak_patterns or not pattern:
        return confidence
    have = set(pattern)
    for note in scorecard.weak_patterns:
        key = note.split(" overconfident", 1)[0].strip()
        flagged = {token for token in key.split("+") if token}
        if flagged and flagged <= have:  # this item exhibits the whole flagged pattern
            return confidence * factor
    return confidence
