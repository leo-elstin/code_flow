"""Tests for the agentic explorer: read-only tools + the tool-calling loop."""

import asyncio
import json
import types

import pytest

from app.agents.roles import explorer as ex
from app.tools import explore_tools


# -- read-only tools ---------------------------------------------------------

def _mkrepo(tmp_path):
    (tmp_path / "lib" / "features").mkdir(parents=True)
    (tmp_path / "lib" / "features" / "book.dart").write_text("class BookPage {}\n// container mix\n")
    (tmp_path / "lib" / "main.dart").write_text("void main() {}\n")
    return str(tmp_path)


def test_list_dir_and_read_and_search(tmp_path):
    repo = _mkrepo(tmp_path)
    fns, _ = explore_tools.make_explore_tools(repo)

    entries = json.loads(fns["list_dir"]("lib"))
    assert "features/" in entries and "main.dart" in entries

    content = fns["read_file"]("lib/features/book.dart")
    assert "BookPage" in content

    hits = json.loads(fns["search_codebase"]("BookPage"))
    assert any(h["file"].endswith("book.dart") for h in hits)


def test_read_file_blocks_traversal(tmp_path):
    repo = _mkrepo(tmp_path)
    fns, _ = explore_tools.make_explore_tools(repo)
    out = fns["read_file"]("../../etc/passwd")
    assert "traversal" in out.lower() or "error" in out.lower()


# -- explorer loop (LLM mocked) ----------------------------------------------

class _FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, **_):
        return {"role": "assistant", "content": self.content}


class _FakeToolCall:
    def __init__(self, cid, name, args):
        self.id = cid
        self.function = types.SimpleNamespace(name=name, arguments=json.dumps(args))


def _resp(msg):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def test_run_explorer_uses_tools_then_returns_findings(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path)
    # Turn 1: model calls search_codebase; Turn 2: returns JSON findings.
    scripted = [
        _resp(_FakeMsg(tool_calls=[_FakeToolCall("c1", "search_codebase", {"query": "container"})])),
        _resp(_FakeMsg(content=json.dumps({
            "relevant_files": [{"path": "lib/features/book.dart", "role": "screen", "why": "book page"}],
            "entry_points": ["lib/main.dart"],
            "key_findings": ["book feature under lib/features"],
            "gaps": [],
        }))),
    ]
    calls = {"n": 0}

    async def fake_acompletion(*, model, messages, **kw):
        r = scripted[calls["n"]]
        calls["n"] += 1
        return r

    monkeypatch.setattr(ex, "acompletion", fake_acompletion)
    monkeypatch.setattr(ex, "append_activity", lambda *a, **k: {})

    findings = asyncio.run(ex.run_explorer("multi-container booking", repo, run_id=None))
    assert calls["n"] == 2  # one tool round + one final
    assert findings["relevant_files"][0]["path"] == "lib/features/book.dart"


def test_run_explorer_survives_llm_failure(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path)

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ex, "acompletion", boom)
    monkeypatch.setattr(ex, "append_activity", lambda *a, **k: {})
    findings = asyncio.run(ex.run_explorer("x", repo, run_id=None))
    assert findings == {}


def test_run_explorer_respects_step_cap(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path)

    # Always calls a tool, never finishes -> must stop at max_steps.
    async def always_tool(*, model, messages, **kw):
        return _resp(_FakeMsg(tool_calls=[_FakeToolCall("c", "list_dir", {"directory": "lib"})]))

    monkeypatch.setattr(ex, "acompletion", always_tool)
    monkeypatch.setattr(ex, "append_activity", lambda *a, **k: {})
    findings = asyncio.run(ex.run_explorer("x", repo, run_id=None, max_steps=3))
    assert findings == {}  # never produced JSON, but returned cleanly


def test_run_explorer_trims_old_tool_results_within_the_loop(tmp_path, monkeypatch):
    """Without per-step trimming, every step resends the whole accumulated
    conversation — each prior read_file result up to 6000 chars — growing
    O(n^2) over the loop. This is the same bug class _trim_dev_messages fixed
    in the dev loop; the explorer loop needs the equivalent."""
    repo = _mkrepo(tmp_path)
    (tmp_path / "lib" / "big.dart").write_text("X" * 2000)

    captured_messages: list[list[dict]] = []
    STEPS_WITH_TOOL_CALLS = 8

    async def fake_acompletion(*, model, messages, **kw):
        captured_messages.append([dict(m) for m in messages])
        step = len(captured_messages)
        if step > STEPS_WITH_TOOL_CALLS:
            return _resp(_FakeMsg(content=json.dumps({
                "relevant_files": [], "entry_points": [], "key_findings": [], "gaps": [],
            })))
        return _resp(_FakeMsg(tool_calls=[_FakeToolCall(f"c{step}", "read_file", {"path": "lib/big.dart"})]))

    monkeypatch.setattr(ex, "acompletion", fake_acompletion)
    monkeypatch.setattr(ex, "append_activity", lambda *a, **k: {})

    asyncio.run(ex.run_explorer("x", repo, run_id=None, max_steps=STEPS_WITH_TOOL_CALLS + 1))

    # By the final call, earlier tool results (beyond the keep-recent window)
    # must already be stubbed — proving trimming ran mid-loop, not just once
    # at the end.
    final_sent = captured_messages[-1]
    tool_msgs = [m for m in final_sent if m.get("role") == "tool"]
    assert len(tool_msgs) == STEPS_WITH_TOOL_CALLS
    stubbed = [m for m in tool_msgs if "older tool output trimmed" in m["content"]]
    full = [m for m in tool_msgs if len(m["content"]) >= 2000]
    assert len(stubbed) > 0
    assert len(full) <= 4
