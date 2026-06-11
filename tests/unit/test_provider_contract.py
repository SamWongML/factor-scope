"""Contract test for the LLMProvider interface.

A provider is anything with ``name``, ``argue``, ``seats``, and ``synthesize``. The deterministic
fake is the default; the real ``claude_code`` provider must satisfy the same shape so it can be
dropped in without touching the orchestrator. DeepSeek is a chore model (off the judgment path), so
it is not a judgment provider. None of these construct a network client at import or selection time.
"""

import pytest

from factor_scope.contract import Band, FactorState, GateState, ListName
from factor_scope.digest import DigestInput, LLMProvider, Side, get_provider
from factor_scope.digest.fake import FakeProvider

pytestmark = pytest.mark.unit


def _brief() -> DigestInput:
    return DigestInput(
        code="X",
        name="X",
        list_name=ListName.HOLDINGS,
        states=(
            FactorState(factor="reversal", level=Band.EXTREME_HIGH, direction="reversal-down"),
        ),
        gate=GateState.OPEN,
    )


def test_fake_satisfies_the_provider_protocol() -> None:
    provider = FakeProvider()
    assert isinstance(provider, LLMProvider)
    assert provider.name == "fake"


def test_seats_argues_both_sides_from_one_brief() -> None:
    # The orchestrator drives the debate through seats(): both seats argued from the same brief,
    # returned in fixed (bull, bear) slots.
    bull, bear = FakeProvider().seats(_brief())
    assert bull.side is Side.BULL
    assert bear.side is Side.BEAR


def test_get_provider_returns_the_fake_by_default() -> None:
    assert isinstance(get_provider("fake"), FakeProvider)


def test_get_provider_claude_code_satisfies_the_protocol() -> None:
    # Selecting the real provider must not require a network client or the CLI to be present.
    provider = get_provider("claude_code")
    assert isinstance(provider, LLMProvider)
    assert provider.name == "claude_code"


def test_claude_code_runs_the_seats_on_the_deep_think_model() -> None:
    from factor_scope.digest.claude_code import ClaudeCodeProvider

    provider = get_provider("claude_code", deep_think_model="opus")
    assert isinstance(provider, ClaudeCodeProvider)
    assert provider._model == "opus"


def test_deepseek_is_not_a_judgment_provider() -> None:
    with pytest.raises(ValueError, match="chore"):
        get_provider("deepseek")


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown"):
        get_provider("bogus")
