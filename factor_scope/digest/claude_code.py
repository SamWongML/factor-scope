"""The real Claude Code (headless) digestion provider — the online default; CI forces the fake.

Judgment stays on Claude Code: a small **bull/bear** team (isolated contexts, consider-the-opposite)
argues both sides, then a synthesis seat nets them. Each seat is a headless ``claude -p ...
--output-format json --json-schema <schema>`` call carrying the structured brief and a
side-specific system prompt loaded from the committed seat definition in
``.claude/agents/{bull,bear,synthesis}.md`` (the single source of truth — the committed agents drive
the seats). The ``--json-schema`` flag **validates the model's reply against the seat's schema and
re-prompts on a mismatch**: on success the validated object lands in the envelope's
``structured_output`` field and parses straight into a
:class:`~factor_scope.digest.provider.Case` / :class:`~factor_scope.digest.provider.Proposal`; if
the retries are exhausted the envelope carries no structured object and the seat degrades to
abstain. The JSON envelope also carries a per-call cost (tokens + USD), which the provider records
as a :class:`~factor_scope.cost.Usage` on :attr:`ClaudeCodeProvider.usage` — tagged ``provider`` /
``model`` so every spend traces to its source — for downstream budget telemetry.

The seats run on the configured **deep-think** model (Claude Opus-class), passed via ``--model``.
The hard guardrails (gate, abstain, scorecard) are still enforced by the orchestrator on top of
whatever the model says — so even an overconfident model can never open the gate. ``subprocess`` and
``json`` are imported lazily and only on a real call, so selecting this provider (or importing the
module) never shells out and the fake-only CI path stays offline.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.contract import LeanAction
from factor_scope.cost import Usage
from factor_scope.digest.provider import Case, DigestInput, Proposal, Side

# The seat system prompts live in the committed agent definitions, never inline here — one source of
# truth, so the prompts actually used and the committed agents can't drift apart.
_AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"


def _load_seat_prompt(name: str) -> str:
    """Load a seat's system prompt from ``.claude/agents/{name}.md`` — the body past frontmatter."""

    lines = (_AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":  # strip the leading YAML frontmatter block
        lines = lines[lines.index("---", 1) + 1 :]
    return "\n".join(lines).strip()


class ClaudeCodeProvider:
    """An :class:`~factor_scope.digest.provider.LLMProvider` backed by headless Claude Code."""

    name = "claude_code"

    def __init__(self, *, model: str | None = None, timeout_s: float = 120.0) -> None:
        self._model = model
        self._timeout_s = timeout_s
        # Per-call cost records, in call order — one per seat turn, for budget telemetry.
        self.usage: list[Usage] = []

    def argue(self, side: Side, brief: DigestInput) -> Case:
        system = _load_seat_prompt("bull" if side is Side.BULL else "bear")
        data = self._complete(system, _brief_prompt(brief), _case_schema())
        return Case(
            side=side,
            strength=max(0.0, _as_float(data.get("strength"))),
            confidence=_clamp01(_as_float(data.get("confidence"))),
            points=_as_str_tuple(data.get("points")),
        )

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        """Run the two seats concurrently — they share the brief but argue in isolated turns."""

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            bull = pool.submit(self.argue, Side.BULL, brief)
            bear = pool.submit(self.argue, Side.BEAR, brief)
            return bull.result(), bear.result()

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        first, second = (bear, bull) if present_bear_first else (bull, bear)
        prompt = f"{_brief_prompt(brief)}\n\n{_case_line(first)}\n{_case_line(second)}"
        data = self._complete(_load_seat_prompt("synthesis"), prompt, _proposal_schema())
        return Proposal(
            action=LeanAction(str(data.get("action", "abstain"))),
            confidence=_clamp01(_as_float(data.get("confidence"))),
            rationale=_as_str_tuple(data.get("rationale")),
            rubric=_as_rubric(data.get("rubric")),
        )

    def _complete(
        self, system: str, prompt: str, schema: dict[str, object]
    ) -> dict[str, object]:
        """Run one headless ``claude -p`` turn, parse its result + cost. Lazy; never run offline.

        ``--json-schema`` asks the CLI to validate the reply against ``schema`` and re-prompt on a
        mismatch; on success the validated object arrives in the envelope's ``structured_output``
        field (see :func:`_parse_result`).
        """

        import json

        cmd = [
            "claude", "-p", prompt,
            "--append-system-prompt", system,
            "--output-format", "json",  # single-object envelope carrying result + cost
            "--json-schema", json.dumps(schema, sort_keys=True),  # validate; re-prompt on miss
        ]
        if self._model:
            cmd += ["--model", self._model]
        parsed, input_tokens, output_tokens, cost_usd = _parse_result(self._invoke(cmd))
        self.usage.append(
            Usage(
                provider=self.name,
                model=self._model or "",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )
        )
        return parsed

    def _invoke(self, cmd: list[str]) -> str:
        """Shell out to the headless ``claude`` CLI, returning stdout. Lazy; never run offline."""

        import subprocess

        completed = subprocess.run(  # noqa: S603 - user-selected provider
            cmd, capture_output=True, text=True, timeout=self._timeout_s, check=True
        )
        return completed.stdout


def _case_schema() -> dict[str, object]:
    """The JSON-schema a bull/bear seat's reply is validated against — the Case fields it carries.

    Only the fields the provider reads are *required*; extra keys are left to pass. Validation is
    re-prompt-on-mismatch, not constrained decoding, so pinning ``additionalProperties: False``
    would turn a chatty reply into a retry-then-abstain — and the coercers already ignore anything
    extra. Range bounds (strength ≥ 0, confidence in [0, 1]) aren't expressed here either: the
    provider clamps untrusted scores at its boundary. The schema asks the shape; the coercers
    guarantee the range.
    """

    return {
        "type": "object",
        "properties": {
            "strength": {"type": "number"},
            "confidence": {"type": "number"},
            "points": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["strength", "confidence", "points"],
    }


def _proposal_schema() -> dict[str, object]:
    """The JSON-schema the synthesis seat's reply is validated against — the Proposal fields.

    ``action`` is pinned to the contract's :class:`~factor_scope.contract.LeanAction` vocabulary
    (single source of truth), so a validated reply can't carry an out-of-vocabulary call. Extra keys
    pass (see :func:`_case_schema` on why ``additionalProperties`` stays open) and scores carry no
    numeric bounds; the provider clamps them.
    """

    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [action.value for action in LeanAction]},
            "confidence": {"type": "number"},
            "rationale": {"type": "array", "items": {"type": "string"}},
            "rubric": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "score": {"type": "number"},
                    },
                    "required": ["criterion", "score"],
                },
            },
        },
        "required": ["action", "confidence", "rationale", "rubric"],
    }


def _parse_result(stdout: str) -> tuple[dict[str, object], int, int, float]:
    """Parse a ``--output-format json`` envelope into the seat's object + its cost.

    The envelope is one JSON object. The validated answer is its ``structured_output`` field
    (present once the reply passes ``--json-schema`` validation) — taken directly, since it already
    conforms to the schema. If it is absent (the seat exhausted its schema-retries, or an older CLI
    that predates structured output), we fall back to slicing the JSON object out of the free-text
    ``result``, tolerating a stray Markdown fence or prose (:func:`_json_object_span`). An envelope
    carrying neither a structured object nor a brace-bearing result raises, and the orchestrator
    degrades the item to abstain (invalid degrades, never aborts).
    """

    import json

    envelope = json.loads(stdout)
    if not isinstance(envelope, dict):
        raise ValueError(f"claude_code: expected a JSON envelope, got {type(envelope).__name__}")

    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        parsed: dict[str, object] = structured
    else:
        text = envelope.get("result")
        if not isinstance(text, str):
            raise ValueError("claude_code: envelope carried no structured_output nor result text")
        loaded = json.loads(_json_object_span(text))
        if not isinstance(loaded, dict):
            raise ValueError(f"claude_code: expected a JSON object, got {type(loaded).__name__}")
        parsed = loaded

    usage = envelope.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return (
        parsed,
        int(_as_float(usage.get("input_tokens"))),
        int(_as_float(usage.get("output_tokens"))),
        _as_float(envelope.get("total_cost_usd")),
    )


def _json_object_span(text: str) -> str:
    """The first ``{`` … last ``}`` slice of a seat reply — drops a Markdown fence or stray prose.

    Only the fallback path (no ``structured_output``) reaches here, on a free-text ``result`` a
    model may have wrapped in a ```json fence or prefaced with a sentence. A bare ``json.loads`` on
    that raises at char 0, degrading the seat to abstain. The reply is one top-level object, so
    slicing to the outermost braces lets the genuine JSON through; a reply with no braces raises a
    ``ValueError`` the orchestrator degrades on (invalid input degrades, never aborts).
    """

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("claude_code: seat reply carried no JSON object")
    return text[start : end + 1]


def _as_float(value: object) -> float:
    """Coerce a parsed JSON scalar to a float, defaulting to 0.0 (model output is untrusted)."""

    return float(value) if isinstance(value, (int, float, str)) else 0.0


def _clamp01(value: float) -> float:
    """Pin an untrusted model score to the contract's [0, 1] bound — a sloppy seat degrades."""

    return max(0.0, min(1.0, value))


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """Coerce a parsed JSON array to a tuple of strings; anything else → empty."""

    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _as_rubric(value: object) -> tuple[tuple[str, float], ...]:
    """Coerce a parsed ``[{"criterion","score"}]`` array into (criterion, score) pairs."""

    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        (str(item["criterion"]), _clamp01(_as_float(item.get("score"))))
        for item in value
        if isinstance(item, dict) and "criterion" in item
    )


def _case_line(case: Case) -> str:
    """One seat's case as a labelled line for the synthesis prompt (order set by the caller)."""

    return (
        f"{case.side.value.upper()} ({case.strength:g}, conf {case.confidence:g}): "
        f"{'; '.join(case.points)}"
    )


def _brief_prompt(brief: DigestInput) -> str:
    """Render the structured brief as plain text for a seat — dated reads, no recalled numbers."""

    lines = [f"Item: {brief.name} ({brief.code}) on the {brief.list_name.value} list."]
    if brief.as_of is not None:
        lines.append(f"As of: {brief.as_of} (reason point-in-time — nothing later is knowable).")
    lines += [f"Trend gate: {brief.gate.value}.", "Factor states:"]
    for state in brief.states:
        if not state.valid:
            continue
        lines.append(f"  - {state.factor}: {state.level.value} ({state.direction})")
    if brief.evidence:
        lines.append("Evidence (dated reads — cite, don't recall):")
        for e in brief.evidence:
            lines.append(f"  - {e.src} ({e.as_of}): {e.one_line}")
    if brief.connections_flag and brief.connections:
        shared = ", ".join(c.shared for c in brief.connections)
        lines.append(f"Look-through overlaps (concentration risk): {shared}.")
    if brief.near_misses:
        lines.append(
            "Near-misses (cannot be promoted — veto context only): "
            + ", ".join(brief.near_misses)
        )
    if brief.scorecard and brief.scorecard.weak_patterns:
        lines.append(f"Mirror — weak patterns: {'; '.join(brief.scorecard.weak_patterns)}.")
    return "\n".join(lines)


__all__ = ["ClaudeCodeProvider"]
