"""Transient-vs-terminal classification for Claude Agent SDK failures.

The strings below are verbatim from real failures observed against the
installed SDK — not invented examples — so this pins the classifier against
what the CLI actually emits."""
from app.agents.sdk_common import is_transient_sdk_error, retry_backoff_seconds


def test_dropped_stream_is_transient():
    assert is_transient_sdk_error(
        "API Error: Connection closed mid-response. The response above may be incomplete."
    )


def test_overload_and_rate_limit_are_transient():
    assert is_transient_sdk_error("Error 529: overloaded_error")
    assert is_transient_sdk_error("rate limit exceeded")
    assert is_transient_sdk_error("Request timed out")


def test_auth_failure_is_not_transient():
    # Retrying this burns tokens and delays a real, actionable error reaching
    # the user — it must surface immediately.
    assert not is_transient_sdk_error("Not logged in · Please run /login")


def test_turn_limit_is_not_transient():
    # A turn cap is a configuration outcome, not a blip; retrying re-runs the
    # whole exploration and hits the same ceiling.
    assert not is_transient_sdk_error("Reached maximum number of turns (20)")


def test_unknown_and_missing_errors_are_not_transient():
    # Allowlist, not denylist: an unrecognized cause fails fast rather than
    # retrying blindly.
    assert not is_transient_sdk_error("Something nobody has seen before")
    assert not is_transient_sdk_error(None)
    assert not is_transient_sdk_error("")


def test_backoff_grows_then_caps():
    assert retry_backoff_seconds(1) == 2.0
    assert retry_backoff_seconds(2) == 4.0
    assert retry_backoff_seconds(3) == 8.0
    assert retry_backoff_seconds(99) == 30.0
