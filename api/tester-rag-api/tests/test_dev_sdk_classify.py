"""Tests for the SDK dev loop's soft-stop / hard-failure classification.

classify_dev_sdk_result is deliberately pure (takes the two fields off
ResultMessage the decision needs, not the SDK object itself) so this doesn't
require claude-agent-sdk installed."""
from app.agents.roles.dev_sdk import classify_dev_sdk_result


def test_turn_limit_is_a_soft_stop():
    assert classify_dev_sdk_result("max_turns", False) == "soft_stop"


def test_budget_limit_is_a_soft_stop():
    assert classify_dev_sdk_result("max_budget_usd", False) == "soft_stop"


def test_soft_stop_reason_wins_even_if_is_error_is_set():
    # A turn-cap result can arrive with is_error=True (the CLI's own exit-code
    # convention for any non-natural stop) — the reason string, not the error
    # flag, is what decides recoverability. Getting this backwards is exactly
    # the bug saga's soft_stop.py documents guarding against for its own
    # SDK-driven agents.
    assert classify_dev_sdk_result("error_max_turns", True) == "soft_stop"


def test_natural_completion_is_success():
    assert classify_dev_sdk_result("end_turn", False) == "success"
    assert classify_dev_sdk_result(None, False) == "success"


def test_unrecognized_error_is_a_hard_failure():
    assert classify_dev_sdk_result("tool_error", True) == "hard_failure"
    assert classify_dev_sdk_result(None, True) == "hard_failure"
