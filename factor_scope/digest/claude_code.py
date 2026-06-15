"""The real Claude Code (headless) digestion provider — the online default; CI forces the fake.

Judgment stays on Claude Code: a small **bull/bear** team (isolated contexts, consider-the-opposite)
argues both sides, then a synthesis seat nets them. Each seat is a headless ``claude -p ...
--output-format stream-json`` call carrying the structured brief and a side-specific system prompt
loaded from the committed seat definition in ``.claude/agents/{bull,bear,synthesis}.md`` (the single
source of truth — the committed agents are what actually drive the seats); the model returns a small
JSON object we parse into a :class:`~factor_scope.digest.provider.Case` /
:class:`~factor_scope.digest.provider.Proposal`. The stream-json transcript's final ``result``
message also carries a per-call cost (tokens + USD), which the provider records as a
:class:`~factor_scope.cost.Usage` on :attr:`ClaudeCodeProvider.usage` — tagged ``provider`` /
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
        data = self._complete(system, _brief_prompt(brief))
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
        data = self._complete(_load_seat_prompt("synthesis"), prompt)
        return Proposal(
            action=LeanAction(str(data.get("action", "abstain"))),
            confidence=_clamp01(_as_float(data.get("confidence"))),
            rationale=_as_str_tuple(data.get("rationale")),
            rubric=_as_rubric(data.get("rubric")),
        )

    def _complete(self, system: str, prompt: str) -> dict[str, object]:
        """Run one headless ``claude -p`` turn, parse its result + cost. Lazy; never run offline."""

        cmd = [
            "claude", "-p", prompt,
            "--append-system-prompt", system,
            "--output-format", "stream-json",
            "--verbose",  # stream-json requires --verbose to emit the full transcript
        ]
        if self._model:
            cmd += ["--model", self._model]
        parsed, input_tokens, output_tokens, cost_usd = _parse_stream_json(self._invoke(cmd))
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


def _parse_stream_json(stdout: str) -> tuple[dict[str, object], int, int, float]:
    """Parse a ``--output-format stream-json`` transcript into the seat's JSON + its cost.

    The transcript is JSONL; its final ``result``-type message carries the assistant text under
    ``result`` (which is itself the seat's small JSON object) plus the call cost (input/output
    tokens + USD). A transcript with no result message, or a result that is not a JSON object,
    raises — the
    orchestrator catches that and degrades the item to abstain (invalid degrades, never aborts).
    """

    import json

    result_msg: dict[str, object] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict) and obj.get("type") == "result":
            result_msg = obj
    if result_msg is None:
        raise ValueError("claude_code: stream-json transcript carried no result message")

    text = result_msg.get("result")
    parsed = json.loads(text) if isinstance(text, str) else text
    if not isinstance(parsed, dict):
        raise ValueError(f"claude_code: expected a JSON object, got {type(parsed).__name__}")
    usage = result_msg.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return (
        parsed,
        int(_as_float(usage.get("input_tokens"))),
        int(_as_float(usage.get("output_tokens"))),
        _as_float(result_msg.get("total_cost_usd")),
    )


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
