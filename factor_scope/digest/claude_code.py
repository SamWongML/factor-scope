"""The real Claude Code (headless) digestion provider — the online default; CI forces the fake.

Judgment stays on Claude Code: a small **bull/bear** team (isolated contexts, consider-the-opposite)
argues both sides, then a synthesis seat nets them. Each seat is a headless ``claude -p ...
--output-format stream-json`` call carrying the structured brief and a side-specific system prompt
loaded from the committed seat definition in ``.claude/agents/{bull,bear,synthesis}.md`` (the single
source of truth — the committed agents are what actually drive the seats); the model returns a small
JSON object we parse into a :class:`~factor_scope.digest.provider.Case` /
:class:`~factor_scope.digest.provider.Proposal`. The stream-json transcript's final ``result``
message also carries a per-call **cost envelope** (tokens + USD), which the provider accumulates on
:attr:`ClaudeCodeProvider.costs` for downstream budget telemetry.

The seats run on the configured **deep-think** model (Claude Opus-class), passed via ``--model``.
The hard guardrails (gate, abstain, scorecard) are still enforced by the orchestrator on top of
whatever the model says — so even an overconfident model can never open the gate. ``subprocess`` and
``json`` are imported lazily and only on a real call, so selecting this provider (or importing the
module) never shells out and the fake-only CI path stays offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factor_scope.contract import LeanAction
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


@dataclass(frozen=True)
class CostEnvelope:
    """One seat call's cost, parsed from the stream-json ``result`` message (consumed by U15)."""

    cost_usd: float
    input_tokens: int
    output_tokens: int
    duration_ms: int


class ClaudeCodeProvider:
    """An :class:`~factor_scope.digest.provider.LLMProvider` backed by headless Claude Code."""

    name = "claude_code"

    def __init__(self, *, model: str | None = None, timeout_s: float = 120.0) -> None:
        self._model = model
        self._timeout_s = timeout_s
        # Per-call cost envelopes, in call order — one per seat turn (consumed by U15 budgeting).
        self.costs: list[CostEnvelope] = []

    def argue(self, side: Side, brief: DigestInput) -> Case:
        system = _load_seat_prompt("bull" if side is Side.BULL else "bear")
        data = self._complete(system, _brief_prompt(brief))
        return Case(
            side=side,
            strength=_as_float(data.get("strength")),
            confidence=_as_float(data.get("confidence")),
            points=_as_str_tuple(data.get("points")),
        )

    def synthesize(self, brief: DigestInput, bull: Case, bear: Case) -> Proposal:
        prompt = (
            f"{_brief_prompt(brief)}\n\nBULL ({bull.strength:g}, conf {bull.confidence:g}): "
            f"{'; '.join(bull.points)}\nBEAR ({bear.strength:g}, conf {bear.confidence:g}): "
            f"{'; '.join(bear.points)}"
        )
        data = self._complete(_load_seat_prompt("synthesis"), prompt)
        return Proposal(
            action=LeanAction(str(data.get("action", "abstain"))),
            confidence=_as_float(data.get("confidence")),
            rationale=_as_str_tuple(data.get("rationale")),
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
        parsed, envelope = _parse_stream_json(self._invoke(cmd))
        self.costs.append(envelope)
        return parsed

    def _invoke(self, cmd: list[str]) -> str:
        """Shell out to the headless ``claude`` CLI, returning stdout. Lazy; never run offline."""

        import subprocess

        completed = subprocess.run(  # noqa: S603 - user-selected provider
            cmd, capture_output=True, text=True, timeout=self._timeout_s, check=True
        )
        return completed.stdout


def _parse_stream_json(stdout: str) -> tuple[dict[str, object], CostEnvelope]:
    """Parse a ``--output-format stream-json`` transcript into the seat's JSON + its cost envelope.

    The transcript is JSONL; its final ``result``-type message carries the assistant text under
    ``result`` (which is itself the seat's small JSON object) plus the cost envelope. A transcript
    with no result message, or a result that is not a JSON object, raises — the orchestrator catches
    that and degrades the item to abstain (invalid degrades, never aborts the run).
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
    envelope = CostEnvelope(
        cost_usd=_as_float(result_msg.get("total_cost_usd")),
        input_tokens=int(_as_float(usage.get("input_tokens"))),
        output_tokens=int(_as_float(usage.get("output_tokens"))),
        duration_ms=int(_as_float(result_msg.get("duration_ms"))),
    )
    return parsed, envelope


def _as_float(value: object) -> float:
    """Coerce a parsed JSON scalar to a float, defaulting to 0.0 (model output is untrusted)."""

    return float(value) if isinstance(value, (int, float, str)) else 0.0


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """Coerce a parsed JSON array to a tuple of strings; anything else → empty."""

    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _brief_prompt(brief: DigestInput) -> str:
    """Render the structured brief as plain text for a seat — dated reads, no recalled numbers."""

    lines = [
        f"Item: {brief.name} ({brief.code}) on the {brief.list_name.value} list.",
        f"Trend gate: {brief.gate.value}.",
        "Factor states:",
    ]
    for state in brief.states:
        if not state.valid:
            continue
        lines.append(f"  - {state.factor}: {state.level.value} ({state.direction})")
    if brief.connections_flag and brief.connections:
        shared = ", ".join(c.shared for c in brief.connections)
        lines.append(f"Look-through overlaps (concentration risk): {shared}.")
    if brief.scorecard and brief.scorecard.weak_patterns:
        lines.append(f"Mirror — weak patterns: {'; '.join(brief.scorecard.weak_patterns)}.")
    return "\n".join(lines)


__all__ = ["ClaudeCodeProvider", "CostEnvelope"]
