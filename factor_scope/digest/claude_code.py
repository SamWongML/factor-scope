"""The real Claude Code (headless) digestion provider — opt-in, never called in CI.

Judgment stays on Claude Code: a small **bull/bear** team (isolated contexts, consider-the-opposite)
argues both sides, then a synthesis seat nets them. Each seat is a headless ``claude -p ...
--output-format json`` call carrying the structured brief and a side-specific system prompt loaded
from the committed seat definition in ``.claude/agents/{bull,bear,synthesis}.md`` (the single source
of truth — the committed agents are what actually drive the seats); the model returns a small JSON
object we parse into a :class:`~factor_scope.digest.provider.Case` /
:class:`~factor_scope.digest.provider.Proposal`.

The hard guardrails (gate, abstain, scorecard) are still enforced by the orchestrator on top of
whatever the model says — so even an overconfident model can never open the gate. ``subprocess`` and
``json`` are imported lazily and only on a real call, so selecting this provider (or importing the
module) never shells out and the fake-only CI path stays offline.
"""

from __future__ import annotations

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


class ClaudeCodeProvider:
    """An :class:`~factor_scope.digest.provider.LLMProvider` backed by headless Claude Code."""

    name = "claude_code"

    def __init__(self, *, model: str | None = None, timeout_s: float = 120.0) -> None:
        self._model = model
        self._timeout_s = timeout_s

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
        """Run one headless ``claude -p`` turn and parse its JSON result. Lazy, opt-in only."""

        import json
        import subprocess

        cmd = ["claude", "-p", prompt, "--append-system-prompt", system, "--output-format", "json"]
        if self._model:
            cmd += ["--model", self._model]
        completed = subprocess.run(  # noqa: S603 - opt-in, user-selected provider
            cmd, capture_output=True, text=True, timeout=self._timeout_s, check=True
        )
        envelope = json.loads(completed.stdout)
        # `--output-format json` wraps the assistant text under "result"; parse that as our JSON.
        result = envelope.get("result", envelope) if isinstance(envelope, dict) else envelope
        parsed = json.loads(result) if isinstance(result, str) else result
        if not isinstance(parsed, dict):
            raise ValueError(f"claude_code: expected a JSON object, got {type(parsed).__name__}")
        return parsed


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


__all__ = ["ClaudeCodeProvider"]
