"""The claude_code seats are driven by the committed ``.claude/agents/*.md`` files.

By design, the bull, bear, and synthesis system prompts have exactly one authoritative
location — the agent files — and the provider loads them at call time. These tests assert the
provider's system-prompt text comes from those files (so the committed agents can't drift silently
from the prompts actually used). The subprocess turn is stubbed, so nothing shells out.
"""

from pathlib import Path

import pytest

from factor_scope.contract import Band, Evidence, FactorState, GateState, ListName
from factor_scope.digest.claude_code import (
    ClaudeCodeProvider,
    CostEnvelope,
    _brief_prompt,
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


def test_brief_prompt_grounds_the_seats_in_dated_evidence_and_as_of() -> None:
    # The seats reason point-in-time: the brief carries its as_of date and the dated, sourced reads
    # behind the item (fetch, don't recall), so a seat can cite a reference instead of a memory.
    brief = DigestInput(
        code="600519",
        name="X",
        list_name=ListName.HOLDINGS,
        states=(FactorState(factor="reversal", level=Band.HIGH, direction="stretched"),),
        gate=GateState.OPEN,
        as_of="2026-06-05",
        evidence=(
            Evidence(src="cninfo", as_of="2026-06-01", one_line="Q1 revenue +30% YoY"),
            Evidence(src="akshare", as_of="2026-06-04", one_line="northbound net buy 3 days"),
        ),
    )
    prompt = _brief_prompt(brief)

    assert "2026-06-05" in prompt  # the point-in-time as_of header
    assert "cninfo" in prompt and "2026-06-01" in prompt and "Q1 revenue +30% YoY" in prompt
    assert "akshare" in prompt and "northbound net buy 3 days" in prompt


def test_brief_prompt_renders_near_misses_as_veto_only_context() -> None:
    # The finalists just below the funnel cut reach the seats as cheap veto context — the bear may
    # cite them — but they are flagged un-promotable, since the gate and funnel stay deterministic.
    brief = DigestInput(
        code="516160",
        name="储能ETF",
        list_name=ListName.EMERGING,
        states=(FactorState(factor="reversal", level=Band.HIGH, direction="stretched"),),
        gate=GateState.OPEN,
        near_misses=("#4 风光储ETF (储能, score 0.41)", "#5 新能源车ETF (储能, score 0.38)"),
    )
    prompt = _brief_prompt(brief)

    assert "Near-misses (cannot be promoted — veto context only):" in prompt
    assert "#4 风光储ETF" in prompt and "#5 新能源车ETF" in prompt


# The rubric criteria the synthesis seat scores against — the committed prompt must document each,
# so the emitted ``rubric`` (read by ``_as_rubric``) can't drift from what the prompt actually asks.
_RUBRIC_CRITERIA = ("evidence", "conviction", "trend/gate", "crowding", "valuation")


def test_synthesis_agent_documents_the_scoring_rubric() -> None:
    body = _agent_body("synthesis").lower()
    assert "rubric" in body  # the output key the provider parses
    for criterion in _RUBRIC_CRITERIA:
        assert criterion in body, f"synthesis rubric must score {criterion!r}"


@pytest.mark.parametrize("name", ["bull", "bear"])
def test_debate_seats_require_reference_grounded_points(name: str) -> None:
    # Each point must cite the dated state/evidence it rests on (reference-grounding).
    assert "cite" in _agent_body(name).lower()


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


def test_seats_runs_both_sides_and_returns_them_in_fixed_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # seats() argues bull and bear (concurrently) and always returns (bull, bear) in that order.
    seen: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(provider, "_complete", lambda system, prompt: seen.append(system) or {})

    bull, bear = provider.seats(_brief())

    assert bull.side is Side.BULL and bear.side is Side.BEAR
    assert sorted(seen) == sorted([_agent_body("bull"), _agent_body("bear")])


def test_synthesize_uses_the_synthesis_agent_file_as_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(provider, "_complete", lambda system, prompt: seen.append(system) or {})

    empty = Case(side=Side.BULL, strength=0.0, confidence=0.0, points=())
    provider.synthesize(_brief(), empty, Case(side=Side.BEAR, strength=0.0, confidence=0.0))

    assert seen == [_agent_body("synthesis")]


def test_synthesis_prompt_order_flips_with_present_bear_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The swap-and-average de-bias presents the cases in both orders; the prompt must honour it.
    prompts: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(provider, "_complete", lambda system, prompt: prompts.append(prompt) or {})
    bull = Case(side=Side.BULL, strength=2.0, confidence=0.7, points=("up",))
    bear = Case(side=Side.BEAR, strength=1.0, confidence=0.6, points=("down",))

    provider.synthesize(_brief(), bull, bear)
    provider.synthesize(_brief(), bull, bear, present_bear_first=True)

    fwd, rev = prompts
    assert fwd.index("BULL") < fwd.index("BEAR")
    assert rev.index("BEAR") < rev.index("BULL")


def test_synthesize_parses_the_optional_rubric(monkeypatch: pytest.MonkeyPatch) -> None:
    # The synthesis seat scores the call against explicit criteria; we parse that into the proposal.
    provider = ClaudeCodeProvider()
    payload = {
        "action": "trim",
        "confidence": 0.5,
        "rubric": [
            {"criterion": "valuation", "score": 0.3},
            {"criterion": "trend/gate posture", "score": 0.6},
        ],
    }
    monkeypatch.setattr(provider, "_complete", lambda system, prompt: payload)

    bull = Case(side=Side.BULL, strength=0.0, confidence=0.0)
    bear = Case(side=Side.BEAR, strength=0.0, confidence=0.0)
    proposal = provider.synthesize(_brief(), bull, bear)

    assert proposal.rubric == (("valuation", 0.3), ("trend/gate posture", 0.6))


def test_seats_clamp_out_of_range_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model output is untrusted: an out-of-range strength/confidence/score is clamped to the
    # contract's bounds at the provider boundary, so a sloppy seat degrades rather than raising a
    # ValidationError when the pipeline builds the bounded artifact models (invalid never raises).
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(
        provider, "_complete", lambda system, prompt: {"strength": -3.0, "confidence": 1.5}
    )
    case = provider.argue(Side.BULL, _brief())
    assert case.strength == 0.0  # a negative case strength clamps up to the floor
    assert case.confidence == 1.0  # an over-unit confidence clamps down to the ceiling

    monkeypatch.setattr(
        provider,
        "_complete",
        lambda system, prompt: {
            "action": "trim",
            "confidence": 9.0,
            "rubric": [
                {"criterion": "valuation", "score": 2.0},
                {"criterion": "crowding", "score": -1.0},
            ],
        },
    )
    proposal = provider.synthesize(
        _brief(),
        Case(side=Side.BULL, strength=0.0, confidence=0.0),
        Case(side=Side.BEAR, strength=0.0, confidence=0.0),
    )
    assert proposal.confidence == 1.0
    assert proposal.rubric == (("valuation", 1.0), ("crowding", 0.0))


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
