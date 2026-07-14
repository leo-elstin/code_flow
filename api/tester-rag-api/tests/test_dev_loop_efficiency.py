"""Tests for the dev-loop efficiency fixes: batch edits, build_runner reuse
signature, and cross-iteration message trimming."""
import os

from app.agents.roles.dev import _trim_dev_messages
from app.tools.dart_tools import dart_source_signature
from app.tools.dev_tools import make_dev_tools


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


def test_trim_dev_messages_preserves_pairing_and_stubs_old_tools():
    # system + (assistant tool_call + tool result) * 15
    messages = [{"role": "system", "content": "sys"}]
    for i in range(15):
        messages.append({"role": "assistant", "content": f"call {i}", "tool_calls": [{"id": str(i)}]})
        messages.append({"role": "tool", "tool_call_id": str(i), "content": "X" * 2000})

    trimmed = _trim_dev_messages(messages)

    # Same number of messages → every tool result keeps its assistant tool_call.
    assert len(trimmed) == len(messages)
    tool_msgs = [m for m in trimmed if m.get("role") == "tool"]
    stubbed = [m for m in tool_msgs if "older tool output trimmed" in m["content"]]
    kept_full = [m for m in tool_msgs if len(m["content"]) == 2000]
    # 15 tool results, keep the most recent 12 in full, stub the oldest 3.
    assert len(kept_full) == 12
    assert len(stubbed) == 3
