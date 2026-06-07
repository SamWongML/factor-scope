"""The claude_code seats are driven by the committed ``.claude/agents/*.md`` files.

By design, the bull, bear, and synthesis system prompts have exactly one authoritative
location — the agent files — and the provider loads them at call time. These tests assert the
provider's system-prompt text comes from those files (so the committed agents can't drift silently
from the prompts actually used). The subprocess turn is stubbed, so nothing shells out.
"""

from pathlib import Path

import pytest

from factor_scope.contract import Band, FactorState, GateState, ListName
from factor_scope.digest.claude_code import ClaudeCodeProvider, _load_seat_prompt
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
