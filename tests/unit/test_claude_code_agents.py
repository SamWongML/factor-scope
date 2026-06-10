"""The claude_code seats are driven by the committed ``.claude/agents/*.md`` files.

By design, the bull, bear, and synthesis system prompts have exactly one authoritative
location — the agent files — and the provider loads them at call time. These tests assert the
provider's system-prompt text comes from those files (so the committed agents can't drift silently
from the prompts actually used). The subprocess turn is stubbed, so nothing shells out.
"""

from pathlib import Path

import pytest

from factor_scope.contract import Band, FactorState, GateState, ListName
from factor_scope.digest.claude_code import (
    ClaudeCodeProvider,
    CostEnvelope,
    _load_seat_prompt,
    _parse_stream_json,
)
from factor_scope.digest.provider import Case, DigestInput, Side

pytestmark = pytest.mark.unit

_AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"


def _agent_body(name: str) -> str:
    """Read the markdown body of an agent file directly, stripping the leading YAML frontmatter."""
    lines = (_AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8").splitlines()
    assert lines[0].strip() == "---", f"{name}.md must open with YAML frontmatter"
    close = lines.index("---", 1)
    return "\n".join(lines[close + 1 :]).strip()


def _brief() -> DigestInput:
    return DigestInput(
        code="X",
        name="X",
        list_name=ListName.HOLDINGS,
        states=(FactorState(factor="reversal", level=Band.HIGH, direction="x", valid=True),),
        gate=GateState.OPEN,
    )


@pytest.mark.parametrize("name", ["bull", "bear", "synthesis"])
def test_each_seat_has_an_agent_definition(name: str) -> None:
    # All three seats — including synthesis — have an authoritative definition file.
    assert (_AGENTS_DIR / f"{name}.md").is_file()
    assert _agent_body(name)  # non-empty system prompt


def test_loader_returns_the_agent_file_body() -> None:
    for name in ("bull", "bear", "synthesis"):
        assert _load_seat_prompt(name) == _agent_body(name)


def test_argue_uses_the_seat_agent_files_as_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(provider, "_complete", lambda system, prompt: seen.append(system) or {})

    provider.argue(Side.BULL, _brief())
    provider.argue(Side.BEAR, _brief())

    assert seen == [_agent_body("bull"), _agent_body("bear")]


def test_synthesize_uses_the_synthesis_agent_file_as_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(provider, "_complete", lambda system, prompt: seen.append(system) or {})

    empty = Case(side=Side.BULL, strength=0.0, confidence=0.0, points=())
    provider.synthesize(_brief(), empty, Case(side=Side.BEAR, strength=0.0, confidence=0.0))

    assert seen == [_agent_body("synthesis")]


# A minimal `--output-format stream-json` transcript: JSONL system/assistant lines then the final
# `result` message carrying the cost envelope and the assistant's parsed JSON under "result".
_STREAM_JSON = "\n".join(
    [
        '{"type":"system","subtype":"init","session_id":"s1"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"thinking"}]}}',
        '{"type":"result","subtype":"success","is_error":false,"duration_ms":1234,'
        '"result":"{\\"strength\\": 2.0, \\"confidence\\": 0.7, \\"points\\": [\\"a\\"]}",'
        '"total_cost_usd":0.0123,"usage":{"input_tokens":150,"output_tokens":40}}',
    ]
)


def test_parse_stream_json_extracts_the_result_and_cost_envelope() -> None:
    parsed, envelope = _parse_stream_json(_STREAM_JSON)
    assert parsed == {"strength": 2.0, "confidence": 0.7, "points": ["a"]}
    assert envelope == CostEnvelope(
        cost_usd=0.0123, input_tokens=150, output_tokens=40, duration_ms=1234
    )


def test_parse_stream_json_rejects_a_transcript_with_no_result_message() -> None:
    with pytest.raises(ValueError, match="result"):
        _parse_stream_json('{"type":"system","subtype":"init"}')


def test_argue_records_the_cost_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(provider, "_invoke", lambda cmd: _STREAM_JSON)

    case = provider.argue(Side.BULL, _brief())

    assert case.strength == 2.0 and case.points == ("a",)
    assert provider.costs == [
        CostEnvelope(cost_usd=0.0123, input_tokens=150, output_tokens=40, duration_ms=1234)
    ]


def test_seats_run_on_the_configured_deep_think_model(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClaudeCodeProvider(model="opus")
    seen: list[list[str]] = []
    monkeypatch.setattr(provider, "_invoke", lambda cmd: seen.append(cmd) or _STREAM_JSON)

    provider.argue(Side.BULL, _brief())

    assert seen and seen[0][-2:] == ["--model", "opus"]
    assert "--output-format" in seen[0]
    assert seen[0][seen[0].index("--output-format") + 1] == "stream-json"
