"""The claude_code seats are driven by the committed ``.claude/agents/*.md`` files.

By design, the bull, bear, and synthesis system prompts have exactly one authoritative
location — the agent files — and the provider loads them at call time. These tests assert the
provider's system-prompt text comes from those files (so the committed agents can't drift silently
from the prompts actually used). The subprocess turn is stubbed, so nothing shells out.
"""

import json
from pathlib import Path

import pytest

from factor_scope.contract import Band, Evidence, FactorState, GateState, LeanAction, ListName
from factor_scope.cost import Usage
from factor_scope.digest.claude_code import (
    ClaudeCodeProvider,
    _brief_prompt,
    _case_schema,
    _load_seat_prompt,
    _parse_result,
    _proposal_schema,
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
    monkeypatch.setattr(
        provider, "_complete", lambda system, prompt, schema: seen.append(system) or {}
    )

    provider.argue(Side.BULL, _brief())
    provider.argue(Side.BEAR, _brief())

    assert seen == [_agent_body("bull"), _agent_body("bear")]


def test_seats_runs_both_sides_and_returns_them_in_fixed_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # seats() argues bull and bear (concurrently) and always returns (bull, bear) in that order.
    seen: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(
        provider, "_complete", lambda system, prompt, schema: seen.append(system) or {}
    )

    bull, bear = provider.seats(_brief())

    assert bull.side is Side.BULL and bear.side is Side.BEAR
    assert sorted(seen) == sorted([_agent_body("bull"), _agent_body("bear")])


def test_synthesize_uses_the_synthesis_agent_file_as_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(
        provider, "_complete", lambda system, prompt, schema: seen.append(system) or {}
    )

    empty = Case(side=Side.BULL, strength=0.0, confidence=0.0, points=())
    provider.synthesize(_brief(), empty, Case(side=Side.BEAR, strength=0.0, confidence=0.0))

    assert seen == [_agent_body("synthesis")]


def test_synthesis_prompt_order_flips_with_present_bear_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The swap-and-average de-bias presents the cases in both orders; the prompt must honour it.
    prompts: list[str] = []
    provider = ClaudeCodeProvider()
    monkeypatch.setattr(
        provider, "_complete", lambda system, prompt, schema: prompts.append(prompt) or {}
    )
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
    monkeypatch.setattr(provider, "_complete", lambda system, prompt, schema: payload)

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
        provider, "_complete", lambda system, prompt, schema: {"strength": -3.0, "confidence": 1.5}
    )
    case = provider.argue(Side.BULL, _brief())
    assert case.strength == 0.0  # a negative case strength clamps up to the floor
    assert case.confidence == 1.0  # an over-unit confidence clamps down to the ceiling

    monkeypatch.setattr(
        provider,
        "_complete",
        lambda system, prompt, schema: {
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


def _result_envelope(
    *,
    structured: object = None,
    result: object = None,
    cost: float = 0.0123,
    usage: dict[str, int] | None = None,
) -> str:
    """A ``--output-format json`` single-object envelope, as the headless CLI emits it.

    ``structured_output`` is the validated object (present once the reply passes ``--json-schema``
    validation); ``result`` is the assistant's free-text answer. The provider prefers the former.
    """
    envelope: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": cost,
        "usage": usage if usage is not None else {"input_tokens": 150, "output_tokens": 40},
    }
    if structured is not None:
        envelope["structured_output"] = structured
    if result is not None:
        envelope["result"] = result
    return json.dumps(envelope)


def test_parse_result_prefers_the_validated_structured_output() -> None:
    # A validated reply lands in ``structured_output``; that is the authoritative field, taken even
    # when the free-text ``result`` mirror is fenced/prose (it can't defeat us).
    parsed, input_tokens, output_tokens, cost_usd = _parse_result(
        _result_envelope(
            structured={"strength": 2.0, "confidence": 0.7, "points": ["a"]},
            result="```json\n{\"strength\": 9.9}\n```",
        )
    )
    assert parsed == {"strength": 2.0, "confidence": 0.7, "points": ["a"]}
    assert (input_tokens, output_tokens, cost_usd) == (150, 40, 0.0123)


@pytest.mark.parametrize(
    "result_text",
    [
        # Fallback layer: an older CLI (or a path that didn't populate structured_output) returns
        # only the free-text result. A capable model still wraps it in a Markdown fence or prose —
        # a bare json.loads on that raises at char 0, so we slice to the outermost object instead.
        '```json\n{"strength": 2.0, "confidence": 0.7, "points": ["a"]}\n```',
        '```\n{"strength": 2.0, "confidence": 0.7, "points": ["a"]}\n```',
        'Here is my assessment:\n{"strength": 2.0, "confidence": 0.7, "points": ["a"]}',
    ],
)
def test_parse_result_falls_back_to_unwrapping_the_free_text_result(result_text: str) -> None:
    parsed, input_tokens, output_tokens, cost_usd = _parse_result(
        _result_envelope(
            result=result_text, cost=0.01, usage={"input_tokens": 10, "output_tokens": 5}
        )
    )
    assert parsed == {"strength": 2.0, "confidence": 0.7, "points": ["a"]}
    assert (input_tokens, output_tokens, cost_usd) == (10, 5, 0.01)


def test_parse_result_rejects_an_envelope_with_no_usable_object() -> None:
    # Neither a structured_output object nor a brace-bearing result (the model refused / returned
    # prose) → raise, so the orchestrator degrades the item to abstain rather than guess a lean.
    with pytest.raises(ValueError, match="object"):
        _parse_result(_result_envelope(result="I cannot answer that."))


def test_case_schema_requires_the_case_fields_and_leaves_extras_open() -> None:
    # The schema requires exactly the Case fields the provider reads, but does not pin
    # additionalProperties: a chatty extra key is left to the coercers, not turned into a retry.
    schema = _case_schema()
    assert "additionalProperties" not in schema
    assert set(schema["required"]) == {"strength", "confidence", "points"}
    assert set(schema["properties"]) == {"strength", "confidence", "points"}


def test_proposal_schema_action_enum_tracks_the_lean_action_contract() -> None:
    # The synthesis action is pinned to the contract's LeanAction values (single source of truth),
    # so a validated reply's action can't fall out of the vocabulary LeanAction(...) expects;
    # additionalProperties stays open so an extra key degrades via the coercers, not a retry.
    schema = _proposal_schema()
    assert "additionalProperties" not in schema
    assert set(schema["required"]) == {"action", "confidence", "rationale", "rubric"}
    assert schema["properties"]["action"]["enum"] == [a.value for a in LeanAction]


def test_argue_records_a_usage_tagged_with_its_provider_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ClaudeCodeProvider(model="opus")
    envelope = _result_envelope(structured={"strength": 2.0, "confidence": 0.7, "points": ["a"]})
    monkeypatch.setattr(provider, "_invoke", lambda cmd: envelope)

    case = provider.argue(Side.BULL, _brief())

    assert case.strength == 2.0 and case.points == ("a",)
    # The cost is tagged with its source of creation (provider + model) — the constant contract.
    assert provider.usage == [
        Usage(
            provider="claude_code",
            model="opus",
            input_tokens=150,
            output_tokens=40,
            cost_usd=0.0123,
        )
    ]


def test_seats_run_with_schema_validation_on_the_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ClaudeCodeProvider(model="opus")
    seen: list[list[str]] = []
    envelope = _result_envelope(structured={"strength": 2.0, "confidence": 0.7, "points": ["a"]})
    monkeypatch.setattr(provider, "_invoke", lambda cmd: seen.append(cmd) or envelope)

    provider.argue(Side.BULL, _brief())

    cmd = seen[0]
    assert cmd[-2:] == ["--model", "opus"]
    # The live invocation asks for a single JSON envelope validated against the seat's schema.
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--json-schema" in cmd
    schema = json.loads(cmd[cmd.index("--json-schema") + 1])
    assert schema == _case_schema()
