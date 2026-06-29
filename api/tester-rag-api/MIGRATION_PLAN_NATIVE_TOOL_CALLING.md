# Migration Plan: JSON ReAct Loop → OpenAI Native Function Calling

## Problem Statement

The Dev agent (`app/agents/roles/dev.py`) uses a hand-rolled JSON ReAct loop:

```
while step < max_steps:          # hard cap at 15
    response = openai.chat(...)  # with response_format={"type": "json_object"}
    action = parse(response)     # model must output {"action": "complete"} to stop
    if action == "complete": break
    execute_tool(action)
```

**Consequences:**
- The model must "remember" to output `"action": "complete"` — it can forget or stall
- `max_steps = 15` is a hard ceiling, not a safety net — complex features silently truncate
- No signal to the verifier that the dev loop exhausted steps vs. completing normally
- History is lost between dev iterations (verifier retries restart from scratch)

**How Claude Code and Cursor solve this:** They use the model's native tool calling API.
The model decides when it's done by returning a response with no tool calls. No sentinel
value, no hard step cap needed.

---

## Current Stack

| Layer | Current |
|---|---|
| Outer orchestration | LangGraph `StateGraph` (keep) |
| Checkpointing | `AsyncSqliteSaver` (keep) |
| LLM client | Raw `AsyncOpenAI` (keep, or wrap with `langchain-openai`) |
| Dev agent inner loop | Custom JSON ReAct while-loop **← migrate this** |
| Tool dispatch | `_execute_dev_tool()` manual dict dispatch **← migrate this** |

---

## Target Architecture

Replace the hand-rolled loop with LangGraph's built-in `create_react_agent`, which uses
OpenAI's native `tools=` parameter. The model calls tools natively and stops when it has
no more tool calls to make.

```
Outer graph (unchanged):
  planner_node → dev_node → verifier_node ⇄ dev_node → qa_node

dev_node (new internals):
  create_react_agent(
      model=ChatOpenAI(model=dev_model),
      tools=[read_file, write_file, edit_file, ...],  ← Python functions as tools
  )
  → model calls tools until it's satisfied, then returns final message
  → no max_steps hard cap; step limit is just a safety net
```

---

## Migration Steps

### Phase 1 — Wrap tools as LangChain `@tool` functions (low risk, no behaviour change)

Each entry in `_execute_dev_tool`'s dispatch dict becomes a proper `@tool`-decorated
function with a docstring (which becomes the tool description the model sees).

**Files to change:** `app/tools/dev_tools.py` (new file)

```python
from langchain_core.tools import tool

@tool
def read_file(path: str) -> str:
    """Read the contents of a file at the given relative path."""
    ...

@tool
def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """Create a NEW file. Fails if the file already exists unless overwrite=True."""
    ...

@tool
def edit_file(path: str, target_content: str, replacement_content: str,
              allow_multiple: bool = False) -> str:
    """Surgically replace a block of code in an existing file."""
    ...

@tool
def analyze_changed_files() -> str:
    """Run flutter analyze scoped to only the planned files."""
    ...

# ... roll_back_file, list_files, search_codebase, lookup_sdk_symbol, run_command
```

Each tool needs access to `worktree_path` and `allowed_paths` (the plan-scoped guard).
Inject these via closure or a per-run tool factory:

```python
def make_dev_tools(worktree_path: str, allowed_paths: set[str], ...) -> list:
    """Factory that returns tools pre-bound to this run's worktree."""
    ...
    return [read_file, write_file, edit_file, ...]
```

### Phase 2 — Replace the while-loop with `create_react_agent`

**File to change:** `app/agents/roles/dev.py` — `run_dev()` function

```python
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from app.tools.dev_tools import make_dev_tools

async def run_dev(...) -> dict[str, Any]:
    tools = make_dev_tools(worktree_path, allowed_paths, ...)

    model = ChatOpenAI(
        model=settings.CODE_AGENT_DEV_MODEL,
        openai_api_key=settings.OPENAI_API_KEY,
    )

    agent = create_react_agent(model=model, tools=tools)

    # Initial messages: system prompt + user context (same content as today)
    input_messages = {
        "messages": [
            SystemMessage(content=DEV_SYSTEM_LOOP),
            HumanMessage(content=build_user_prompt(plan, context_bundle, brief, sdk_hints)),
        ]
    }

    result = await agent.ainvoke(
        input_messages,
        config={"recursion_limit": 40},  # safety net, not a real constraint
    )

    # Final message is the model's completion summary
    dev_summary = result["messages"][-1].content
    ...
```

`recursion_limit=40` replaces `max_steps=15` — it's a safety net, not the primary
termination mechanism. The model stops naturally when it has nothing more to do.

### Phase 3 — Activity feed integration

`create_react_agent` emits LangGraph events. Stream them to hook into `append_activity`:

```python
async for event in agent.astream_events(input_messages, version="v2", config=config):
    kind = event["event"]
    if kind == "on_tool_start":
        append_activity(run_id, type="tool", phase="dev",
                        title=f"Called tool: {event['name']}", ...)
    elif kind == "on_chat_model_stream":
        # stream thinking tokens if desired
        pass
    elif kind == "on_tool_end":
        append_activity(run_id, type="tool", phase="dev",
                        title=f"Tool result: {event['name']}", ...)
```

This preserves the existing activity log UX with no Flutter UI changes needed.

### Phase 4 — Mark truncation explicitly

If the recursion limit is hit (rare), LangGraph raises `GraphRecursionError`. Catch it
and set a `truncated=True` flag in the return dict so the verifier can surface it:

```python
from langgraph.errors import GraphRecursionError

try:
    result = await agent.ainvoke(...)
    truncated = False
except GraphRecursionError:
    truncated = True
    dev_summary = "Dev agent hit step limit — partial implementation."

return {
    "file_changes": file_changes,
    "summary": dev_summary,
    "truncated": truncated,   # ← new
    ...
}
```

The verifier can then include this in its report rather than silently passing partial work.

---

## Dependency Change

Add `langchain-openai` to `pyproject.toml`:

```toml
dependencies = [
    ...
    "langchain-openai",   # ← add
    "langchain-core",     # ← add (likely transitive, but pin explicitly)
]
```

`langgraph` is already a dependency. No other changes to `pyproject.toml`.

---

## What Does NOT Change

- The outer LangGraph `StateGraph` (planner → dev → verifier → qa) — unchanged
- `AsyncSqliteSaver` checkpointing — unchanged
- `FeatureRunState` — add `truncated: bool` field only
- All other agents (planner, verifier, qa) — they already use `chat_completion_json`
  which is fine for single-shot structured output; no need to migrate them
- Flutter UI — activity log already works off `append_activity` events
- `_finalize_dev_changes()` post-processing (stage files, build_runner) — unchanged

---

## Risk & Rollback

| Risk | Mitigation |
|---|---|
| Tool schema mismatch (model calls tool with wrong args) | Pydantic validation in `@tool` signatures; errors returned as tool result, model self-corrects |
| Context window growth (full message history) | `recursion_limit=40` caps total turns; same as today's 15 but with headroom |
| Behaviour regression | Keep old `run_dev_legacy()` behind a feature flag (`DEV_AGENT_LEGACY=true`) for one sprint |
| LangChain version conflicts | Pin `langchain-openai>=0.2`, `langchain-core>=0.3` |

---

## Effort Estimate

| Phase | Effort | Risk |
|---|---|---|
| Phase 1 — Wrap tools | ~4h | Low — pure refactor, no logic change |
| Phase 2 — Replace loop | ~3h | Medium — main behaviour change |
| Phase 3 — Activity feed | ~2h | Low — additive only |
| Phase 4 — Truncation flag | ~1h | Low — additive only |
| Testing & validation | ~4h | — |
| **Total** | **~14h** | |

---

## Success Criteria

1. Dev agent completes a 5-file feature without hitting step limit
2. `max_steps` / `recursion_limit` is never the primary exit path in normal runs
3. Activity log in Flutter UI shows same tool call events as today
4. `truncated=True` appears in verifier report when step limit is genuinely hit
5. All existing tests in `tests/test_dev_guardrails.py` pass
