"""Tests for the dev-loop efficiency fixes: batch edits, build_runner reuse
signature, and cross-iteration message trimming."""
import asyncio
import copy
import json
import os
import uuid

from app.agents.roles import dev as dev_mod
from app.agents.roles.dev import _trim_dev_messages, run_dev
from app.services import run_activity
from app.services.llm_providers.base import (
    AssistantMessage,
    ChatCompletionResponse,
    Choice,
    FunctionCall,
    ToolCall,
    Usage,
)
from app.tools.dart_tools import dart_source_signature
from app.tools.dev_tools import make_dev_tools
from app.tools.grep import read_file


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def test_apply_edits_batches_across_files(tmp_path):
    root = str(tmp_path)
    _write(os.path.join(root, "lib/a.dart"), "int a = 1;\n")
    _write(os.path.join(root, "lib/b.dart"), "int b = 1;\n")
    functions, _ = make_dev_tools(
        worktree_path=root,
        allowed_paths={"lib/a.dart", "lib/b.dart"},
        analyze_targets=["lib/a.dart", "lib/b.dart"],
        allow_flutter_test=False,
        planned_test_files=[],
    )
    result = functions["apply_edits_tool"](
        edits=[
            {"path": "lib/a.dart", "target_content": "int a = 1;", "replacement_content": "int a = 2;"},
            {"path": "lib/b.dart", "target_content": "int b = 1;", "replacement_content": "int b = 2;"},
        ]
    )
    assert "Applied 2/2" in result
    assert open(os.path.join(root, "lib/a.dart")).read() == "int a = 2;\n"
    assert open(os.path.join(root, "lib/b.dart")).read() == "int b = 2;\n"


def test_apply_edits_reports_per_edit_and_blocks_out_of_scope(tmp_path):
    root = str(tmp_path)
    _write(os.path.join(root, "lib/a.dart"), "int a = 1;\n")
    _write(os.path.join(root, "lib/secret.dart"), "int s = 1;\n")
    functions, _ = make_dev_tools(
        worktree_path=root,
        allowed_paths={"lib/a.dart"},  # secret.dart is NOT in plan
        analyze_targets=["lib/a.dart"],
        allow_flutter_test=False,
        planned_test_files=[],
    )
    result = functions["apply_edits_tool"](
        edits=[
            {"path": "lib/a.dart", "target_content": "int a = 1;", "replacement_content": "int a = 9;"},
            {"path": "lib/secret.dart", "target_content": "int s = 1;", "replacement_content": "int s = 9;"},
            {"path": "lib/a.dart", "target_content": "does-not-exist", "replacement_content": "x"},
        ]
    )
    assert "Applied 1/3" in result
    assert "BLOCKED" in result  # out-of-scope edit reported, not applied
    assert open(os.path.join(root, "lib/secret.dart")).read() == "int s = 1;\n"  # untouched
    assert open(os.path.join(root, "lib/a.dart")).read() == "int a = 9;\n"


def test_dart_source_signature_changes_with_content_and_ignores_generated(tmp_path):
    root = str(tmp_path)
    _write(os.path.join(root, "lib/model.dart"), "class M {}\n")
    _write(os.path.join(root, "lib/model.g.dart"), "// generated v1\n")

    paths = ["lib/model.dart", "lib/model.g.dart"]
    sig1 = dart_source_signature(root, paths)

    # Regenerating the generated file must NOT change the signature.
    _write(os.path.join(root, "lib/model.g.dart"), "// generated v2 totally different\n")
    assert dart_source_signature(root, paths) == sig1

    # Editing the source file MUST change it.
    _write(os.path.join(root, "lib/model.dart"), "class M { int x = 0; }\n")
    assert dart_source_signature(root, paths) != sig1


def _messages_with_n_tool_calls(n: int) -> list[dict]:
    messages = [{"role": "system", "content": "sys"}]
    for i in range(n):
        messages.append({"role": "assistant", "content": f"call {i}", "tool_calls": [{"id": str(i)}]})
        messages.append({"role": "tool", "tool_call_id": str(i), "content": "X" * 2000})
    return messages


def test_trim_dev_messages_preserves_pairing_and_stubs_old_tools():
    # 40 tool results: 28 eligible for trimming beyond the recent-12 window,
    # rounded down to the nearest batch of 24 → 24 stale, 16 full.
    messages = _messages_with_n_tool_calls(40)

    trimmed = _trim_dev_messages(messages)

    # Same number of messages → every tool result keeps its assistant tool_call.
    assert len(trimmed) == len(messages)
    tool_msgs = [m for m in trimmed if m.get("role") == "tool"]
    stubbed = [m for m in tool_msgs if "older tool output trimmed" in m["content"]]
    kept_full = [m for m in tool_msgs if len(m["content"]) == 2000]
    assert len(kept_full) == 16
    assert len(stubbed) == 24


def test_trim_dev_messages_holds_the_cutoff_steady_within_a_batch():
    """The whole point of batching: adding a few more tool calls that don't
    cross the next batch boundary must not change which earlier messages get
    stubbed, so the resent prefix stays byte-identical and prompt caching
    (Anthropic cache_control / OpenAI automatic prefix match) can actually
    hit across that stretch instead of invalidating on every single call.

    This range also mimics a real step adding a burst of tool calls at once
    (the "batch independent tool calls into one turn" prompt instruction
    routinely produces 5-11 in one step) — the cutoff must survive that
    without moving, which is what defeated the first version of this fix."""
    # 36 through 59 all round down to the same batch boundary (eligible
    # 24-47, all floor to 24), so the stale set — and therefore every
    # already-stubbed message's content — must be identical across this
    # whole range, including jumps as big as a real multi-call step.
    baseline = _trim_dev_messages(_messages_with_n_tool_calls(36))

    for n in (40, 45, 47, 50, 55, 59):
        trimmed = _trim_dev_messages(_messages_with_n_tool_calls(n))
        # Compare only the messages present in both (the shared prefix).
        assert trimmed[: len(baseline)] == baseline

    # Crossing into the next batch (60: eligible 48, floors to 48) stubs more.
    next_batch = _trim_dev_messages(_messages_with_n_tool_calls(60))
    next_batch_stubbed = sum(
        1 for m in next_batch if m.get("role") == "tool" and "older tool output trimmed" in m["content"]
    )
    assert next_batch_stubbed == 48


def test_run_dev_trims_mid_loop_not_just_at_boundaries(tmp_path, monkeypatch):
    """A single long-running loop must stay bounded on its own.

    _trim_dev_messages previously only ran at run *boundaries* (continuation
    start, final persistence), so a run that made many tool calls within one
    run_dev() call — never touching those boundaries — kept its conversation
    growing unstubbed for its entire lifetime. This is exactly what happened
    on ticket MMA-3481: 66 tool calls across 10 steps, all in one run, before
    it hit a token-per-minute rate limit."""
    worktree = tmp_path / "worktree"
    (worktree / "lib").mkdir(parents=True)
    (worktree / "lib" / "big.dart").write_text("X" * 3000, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    plan = {"files_to_create": [{"path": "lib/big.dart"}], "files_to_modify": []}

    STEPS_WITH_TOOL_CALLS = 36
    captured_messages: list[list[dict]] = []

    async def fake_acompletion(*, model, messages, tools, tool_choice, **kwargs):
        # Snapshot BEFORE this call mutates further — proves what the loop
        # actually sent on the wire at this step, not just the final state.
        captured_messages.append(copy.deepcopy(messages))
        step = len(captured_messages)

        if step > STEPS_WITH_TOOL_CALLS:
            return ChatCompletionResponse(
                choices=[Choice(message=AssistantMessage(content="Done."), finish_reason="stop")],
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        return ChatCompletionResponse(
            choices=[
                Choice(
                    message=AssistantMessage(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                id=f"call_{step}",
                                function=FunctionCall(
                                    name="read_file_tool",
                                    arguments=json.dumps({"path": "lib/big.dart"}),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(prompt_tokens=100 * step, completion_tokens=20, total_tokens=100 * step + 20),
        )

    monkeypatch.setattr(dev_mod, "acompletion", fake_acompletion)

    result = asyncio.run(
        run_dev(
            plan=plan,
            context_bundle={},
            worktree_path=str(worktree),
            project_path=str(project),
            run_id=None,
        )
    )

    assert not result["truncated"]
    assert len(captured_messages) == STEPS_WITH_TOOL_CALLS + 1

    def stubbed_and_full_counts(messages):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        stubbed = sum(1 for m in tool_msgs if "older tool output trimmed" in m["content"])
        # A stub is content[:400] + a suffix, so it's still >400 chars — "full"
        # means untouched original content, i.e. simply not stubbed.
        full = len(tool_msgs) - stubbed
        return stubbed, full

    # Early on (fewer than 12 tool results so far), nothing is stubbed yet.
    early_stubbed, early_full = stubbed_and_full_counts(captured_messages[5])
    assert early_stubbed == 0

    # By the last step — still *inside the same run*, no continuation/retry
    # boundary crossed — older results are already stubbed. This is the
    # actual bug: proving it happens before the loop ends, not after.
    late_stubbed, late_full = stubbed_and_full_counts(captured_messages[-1])
    assert late_stubbed > 0
    assert late_full <= 12


def test_run_dev_logs_per_step_token_usage(tmp_path, monkeypatch):
    """acompletion() in the dev tool-calling loop previously logged no token
    activity at all — only chat_completion_json() did — so a run like
    MMA-3481's had no per-step token curve to diagnose after it failed on a
    rate limit. Each dev-loop step must now append a 'token' activity event."""
    monkeypatch.setattr(run_activity, "_DB_PATH", tmp_path / "activity.db")

    worktree = tmp_path / "worktree"
    (worktree / "lib").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    plan = {"files_to_create": [], "files_to_modify": []}
    run_id = str(uuid.uuid4())

    call_count = 0
    seen_cache_keys = []

    async def fake_acompletion(*, model, messages, tools, tool_choice, **kwargs):
        nonlocal call_count
        call_count += 1
        seen_cache_keys.append(kwargs.get("prompt_cache_key"))
        if call_count > 3:
            return ChatCompletionResponse(
                choices=[Choice(message=AssistantMessage(content="Done."), finish_reason="stop")],
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
        return ChatCompletionResponse(
            choices=[
                Choice(
                    message=AssistantMessage(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                id=f"call_{call_count}",
                                function=FunctionCall(name="unknown_tool", arguments="{}"),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(
                prompt_tokens=1000 * call_count,
                completion_tokens=50,
                total_tokens=1000 * call_count + 50,
                # Only step 2 "hits" a warm cache — proves cached_tokens flows
                # from the response all the way into the activity log's meta.
                cached_tokens=800 if call_count == 2 else 0,
            ),
        )

    monkeypatch.setattr(dev_mod, "acompletion", fake_acompletion)

    asyncio.run(
        run_dev(
            plan=plan,
            context_bundle={},
            worktree_path=str(worktree),
            project_path=str(project),
            run_id=run_id,
        )
    )

    events = run_activity.list_activity(run_id)
    token_events = [e for e in events if e["type"] == "token"]
    # 3 tool-calling steps + 1 final natural-completion step = 4 LLM calls.
    assert len(token_events) == 4
    assert [e["meta"]["total_tokens"] for e in token_events] == [1050, 2050, 3050, 15]
    assert [e["meta"]["cached_tokens"] for e in token_events] == [0, 800, 0, 0]

    totals = run_activity.get_token_totals(run_id)
    assert totals["total_tokens"] == 1050 + 2050 + 3050 + 15
    assert totals["cached_tokens"] == 800

    # prompt_cache_key=run_id was passed on every call, for cache-shard stability.
    assert seen_cache_keys == [run_id, run_id, run_id, run_id]


def test_run_dev_executes_batched_writes_from_one_turn(tmp_path, monkeypatch):
    """The prompt now tells the model to create all planned files in ONE turn.

    That only saves anything if the loop actually executes every tool call in
    a multi-call turn and pairs each result with its own tool_call_id. On
    ticket MMA-3481 the model wrote one file per turn (steps 8 and 9 of 10),
    so a 4-file plan cost 4 round trips — each one re-sending the whole
    accumulated conversation — and it died on the rate limit before finishing.
    """
    worktree = tmp_path / "worktree"
    (worktree / "test").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()

    planned = [
        "test/a_cubit_test.dart",
        "test/b_validator_test.dart",
        "test/c_mapper_test.dart",
        "test/d_widget_test.dart",
    ]
    plan = {"files_to_create": [{"path": p} for p in planned], "files_to_modify": []}

    turns = 0
    sent_after_writes = []

    async def fake_acompletion(*, model, messages, tools, tool_choice, **kwargs):
        nonlocal turns
        turns += 1
        if turns > 1:
            sent_after_writes.append(copy.deepcopy(messages))
            return ChatCompletionResponse(
                choices=[Choice(message=AssistantMessage(content="All four created."), finish_reason="stop")],
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        # One turn, four independent write_file calls — what the prompt asks for.
        return ChatCompletionResponse(
            choices=[
                Choice(
                    message=AssistantMessage(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                id=f"call_{i}",
                                function=FunctionCall(
                                    name="write_file_tool",
                                    arguments=json.dumps(
                                        {"path": path, "content": f"// {path}\n"}
                                    ),
                                ),
                            )
                            for i, path in enumerate(planned)
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        )

    monkeypatch.setattr(dev_mod, "acompletion", fake_acompletion)

    result = asyncio.run(
        run_dev(
            plan=plan,
            context_bundle={},
            worktree_path=str(worktree),
            project_path=str(project),
            run_id=None,
        )
    )

    # All four files actually written — from a single model turn.
    for path in planned:
        assert (worktree / path).read_text() == f"// {path}\n"
    assert turns == 2, "four files should not have needed more than one write turn"

    # Each write got its own correctly-correlated tool result. Mispairing these
    # is what would break the conversation on the very next request.
    follow_up = sent_after_writes[0]
    tool_msgs = [m for m in follow_up if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == [f"call_{i}" for i in range(4)]
    assert all("Successfully wrote" in m["content"] for m in tool_msgs)


# --- read_file windowing ----------------------------------------------------
# read_file previously had no way to read part of a file — full file (up to a
# 120k-char/~30k-token cap) or nothing. A tool-calling loop that reads several
# real production files in full can burn its context on file dumps alone.


def _write_numbered_lines(path, count):
    path.write_text("\n".join(f"line {i}" for i in range(1, count + 1)) + "\n", encoding="utf-8")


def test_read_file_default_behavior_is_unchanged(tmp_path):
    """Every existing caller (planner, explorer, feature_discovery) reads
    without offset/limit — that path must return exactly what it always did."""
    target = tmp_path / "a.dart"
    target.write_text("hello world\n", encoding="utf-8")
    assert read_file(str(tmp_path), "a.dart") == "hello world\n"


def test_read_file_offset_and_limit_returns_requested_window(tmp_path):
    target = tmp_path / "big.dart"
    _write_numbered_lines(target, 100)

    result = read_file(str(tmp_path), "big.dart", offset=10, limit=5)

    assert result.startswith("[lines 10-14 of 100]\n")
    body = result.split("\n", 1)[1]
    assert body.splitlines() == [f"line {i}" for i in range(10, 15)]


def test_read_file_offset_without_limit_reads_to_end(tmp_path):
    target = tmp_path / "big.dart"
    _write_numbered_lines(target, 20)

    result = read_file(str(tmp_path), "big.dart", offset=18)

    assert result.startswith("[lines 18-20 of 20]\n")
    body = result.split("\n", 1)[1]
    assert body.splitlines() == ["line 18", "line 19", "line 20"]


def test_read_file_windowed_result_is_far_smaller_than_full_read(tmp_path):
    """The actual point: reading one method out of a big file must cost a lot
    fewer tokens than reading the whole thing."""
    target = tmp_path / "huge.dart"
    _write_numbered_lines(target, 5000)

    full = read_file(str(tmp_path), "huge.dart")
    windowed = read_file(str(tmp_path), "huge.dart", offset=2500, limit=20)

    assert len(windowed) < len(full) / 50


def test_read_file_tool_forwards_offset_and_limit(tmp_path):
    _write_numbered_lines(tmp_path / "big.dart", 50)
    functions, _ = make_dev_tools(
        worktree_path=str(tmp_path),
        allowed_paths=set(),
        analyze_targets=[],
        allow_flutter_test=False,
        planned_test_files=[],
    )

    result = functions["read_file_tool"](path="big.dart", offset=5, limit=3)

    assert result.startswith("[lines 5-7 of 50]\n")
    assert result.split("\n", 1)[1].splitlines() == ["line 5", "line 6", "line 7"]


def test_read_file_tool_without_offset_limit_reads_whole_file(tmp_path):
    (tmp_path / "small.dart").write_text("class A {}\n", encoding="utf-8")
    functions, _ = make_dev_tools(
        worktree_path=str(tmp_path),
        allowed_paths=set(),
        analyze_targets=[],
        allow_flutter_test=False,
        planned_test_files=[],
    )

    assert functions["read_file_tool"](path="small.dart") == "class A {}\n"


# --- dev prompt no longer duplicates plan_markdown --------------------------


def test_dev_prompt_excludes_plan_markdown_but_keeps_structured_fields(tmp_path, monkeypatch):
    """plan_markdown is a prose restatement of fields already in the same
    plan dict (feature_summary, architecture, ...) — it's for the human plan
    review UI, not the dev loop, and was being resent on every single turn."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    plan = {
        "feature_summary": "Add the frobnicator widget.",
        "files_to_create": [],
        "files_to_modify": [],
        "plan_markdown": "# Frobnicator\n\nA VERY long human-readable restatement " + ("x" * 2000),
    }

    captured = {}

    async def fake_acompletion(*, model, messages, tools, tool_choice, **kwargs):
        captured["messages"] = messages
        return ChatCompletionResponse(
            choices=[Choice(message=AssistantMessage(content="Done."), finish_reason="stop")],
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    monkeypatch.setattr(dev_mod, "acompletion", fake_acompletion)

    asyncio.run(
        run_dev(
            plan=plan,
            context_bundle={},
            worktree_path=str(worktree),
            project_path=str(project),
            run_id=None,
        )
    )

    first_user_message = captured["messages"][1]["content"]
    assert "frobnicator widget" in first_user_message  # structured field kept
    assert "VERY long human-readable restatement" not in first_user_message  # markdown dropped
