"""Per-task reasoning tiers — cost follows difficulty (ROADMAP §8–§9).

Reasoning is split into difficulty tiers so the expensive deep-think seat is reserved for the one
hard call (the final bull/bear→synthesis debate) and the bulk jobs run cheap-first on DeepSeek V4
Flash. The routing lives on :class:`~factor_scope.config.Config`; these tests pin the default routes
and assert the tiers resolve to *explicit* model IDs (the ``deepseek-chat`` / ``deepseek-reasoner``
aliases deprecate 2026-07-24, so they must never appear).
"""

import pytest

from factor_scope.config import (
    TASK_BULK,
    TASK_DEBATE,
    TIER_DEEP_THINK,
    TIER_FLASH,
    TIER_PRO,
    Config,
)
from factor_scope.digest.deepseek import DEFAULT_MODEL

pytestmark = pytest.mark.unit


def test_final_debate_routes_to_deep_think() -> None:
    assert Config().tier_for_task(TASK_DEBATE) is TIER_DEEP_THINK


def test_bulk_routes_to_flash() -> None:
    assert Config().tier_for_task(TASK_BULK) is TIER_FLASH


def test_each_tier_resolves_to_an_explicit_model_id() -> None:
    tiers = Config().reasoning_tiers
    assert tiers[TIER_FLASH] == "deepseek-v4-flash"
    assert tiers[TIER_PRO] == "deepseek-v4-pro"
    # The deep-think seats run on a Claude Opus-class model via the headless `claude` CLI.
    assert tiers[TIER_DEEP_THINK] == "opus"


def test_model_for_task_resolves_through_the_route() -> None:
    config = Config()
    assert config.model_for_task(TASK_DEBATE) == "opus"
    assert config.model_for_task(TASK_BULK) == "deepseek-v4-flash"


def test_deepseek_chore_default_is_an_explicit_v4_id_not_a_deprecated_alias() -> None:
    # The legacy `deepseek-chat` / `deepseek-reasoner` aliases deprecate 2026-07-24; the chore
    # client defaults to the Flash tier (the full tier→id registry is asserted above).
    assert DEFAULT_MODEL == "deepseek-v4-flash"
    assert "chat" not in DEFAULT_MODEL and "reasoner" not in DEFAULT_MODEL
