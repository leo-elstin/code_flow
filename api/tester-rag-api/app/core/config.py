import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    OPENAI_CHAT_MODEL: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-5.5")
    # Dev/codegen loop model. Defaults to the main chat model so the loop never
    # silently degrades to a weak model when the override is unset; set
    # CODE_AGENT_DEV_MODEL explicitly to run a lighter/faster model.
    CODE_AGENT_DEV_MODEL: str = os.getenv(
        "CODE_AGENT_DEV_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-5.5")
    )
    OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    LITELLM_API_BASE: str | None = os.getenv("LITELLM_API_BASE")
    LITELLM_VERBOSE: bool = os.getenv("LITELLM_VERBOSE", "false").lower() in ("1", "true", "yes")

    # Jira write-back: disabled by default so agent runs never mutate Jira tickets.
    # Set JIRA_WRITE_ENABLED=true to allow the pipeline to push status transitions
    # back to Jira Cloud.
    JIRA_WRITE_ENABLED: bool = os.getenv("JIRA_WRITE_ENABLED", "false").lower() in ("1", "true", "yes")
    INGEST_EMBEDDING_BATCH_SIZE: int = int(os.getenv("INGEST_EMBEDDING_BATCH_SIZE", "50"))
    INGEST_EMBEDDING_CONCURRENCY: int = int(os.getenv("INGEST_EMBEDDING_CONCURRENCY", "1"))

    CODE_AGENT_DATA_DIR: str = os.path.abspath(os.getenv("CODE_AGENT_DATA_DIR", "./data"))
    CODE_AGENT_CHECKPOINT_DB: str = os.path.abspath(os.getenv(
        "CODE_AGENT_CHECKPOINT_DB", "./data/code_agent_runs.db"
    ))
    CODE_AGENT_WORKTREES_DIR: str = os.path.abspath(os.getenv("CODE_AGENT_WORKTREES_DIR", "./data/worktrees"))
    CODE_AGENT_MAX_VERIFIER_ITERATIONS: int = int(
        os.getenv("CODE_AGENT_MAX_VERIFIER_ITERATIONS", "3")
    )
    CODE_AGENT_MAX_MANUAL_RETRY_ITERATIONS: int = int(
        os.getenv("CODE_AGENT_MAX_MANUAL_RETRY_ITERATIONS", "3")
    )
    CODE_AGENT_DART_ANALYZE_TIMEOUT: int = int(os.getenv("CODE_AGENT_DART_ANALYZE_TIMEOUT", "120"))
    CODE_AGENT_BUILD_RUNNER_TIMEOUT: int = int(
        os.getenv("CODE_AGENT_BUILD_RUNNER_TIMEOUT", "300")
    )
    CODE_AGENT_RUN_BUILD_RUNNER: bool = os.getenv("CODE_AGENT_RUN_BUILD_RUNNER", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    CODE_AGENT_PUB_GET_TIMEOUT: int = int(os.getenv("CODE_AGENT_PUB_GET_TIMEOUT", "180"))
    CODE_AGENT_USE_LSP: bool = os.getenv("CODE_AGENT_USE_LSP", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # When true, Jira tickets named in a ticket's own text (e.g. a cross-project
    # rule ticket like RULE-667) are fetched and their descriptions handed to the
    # planner, so it can plan without pausing to ask about them.
    CODE_AGENT_RESOLVE_REFERENCED_TICKETS: bool = os.getenv(
        "CODE_AGENT_RESOLVE_REFERENCED_TICKETS", "true"
    ).lower() in ("1", "true", "yes")
    CODE_AGENT_MAX_REFERENCED_TICKETS: int = int(
        os.getenv("CODE_AGENT_MAX_REFERENCED_TICKETS", "5")
    )
    CODE_AGENT_LSP_TIMEOUT: int = int(os.getenv("CODE_AGENT_LSP_TIMEOUT", "30"))
    # Agentic discovery: run a read-only explorer tool loop before planning that
    # locates the concrete files a plan needs (Cursor-style), so the planner can
    # produce a file-accurate plan instead of stopping to ask "which files?".
    CODE_AGENT_AGENTIC_DISCOVERY: bool = os.getenv(
        "CODE_AGENT_AGENTIC_DISCOVERY", "true"
    ).lower() in ("1", "true", "yes")
    CODE_AGENT_EXPLORER_MAX_STEPS: int = int(os.getenv("CODE_AGENT_EXPLORER_MAX_STEPS", "10"))
    # How many explorer-found files to read into the planner context.
    CODE_AGENT_EXPLORER_MAX_FILES: int = int(os.getenv("CODE_AGENT_EXPLORER_MAX_FILES", "8"))
    # Prior-work discovery: before planning, check run history + git branches for
    # earlier attempts on the same ticket (or a same-titled ticket) so the planner
    # can build on existing work instead of re-implementing it from scratch.
    CODE_AGENT_PRIOR_WORK_DISCOVERY: bool = os.getenv(
        "CODE_AGENT_PRIOR_WORK_DISCOVERY", "true"
    ).lower() in ("1", "true", "yes")
    # Per-request LLM timeout (seconds) and retry count. Without a timeout a
    # stalled provider response hangs the whole run (planner/dev/verifier/qa)
    # indefinitely, so this bounds every completion call.
    CODE_AGENT_LLM_TIMEOUT: int = int(os.getenv("CODE_AGENT_LLM_TIMEOUT", "120"))
    CODE_AGENT_LLM_MAX_RETRIES: int = int(os.getenv("CODE_AGENT_LLM_MAX_RETRIES", "1"))
    # Anthropic requires max_tokens on every request (OpenAI treats it as
    # optional), so the Anthropic client needs a default. 8192 is accepted by
    # every current Claude model and leaves the dev loop room to write files.
    CODE_AGENT_LLM_MAX_TOKENS: int = int(os.getenv("CODE_AGENT_LLM_MAX_TOKENS", "8192"))
    # Timeout (seconds) for Jira REST calls so referenced-ticket resolution and
    # sync can never hang a run.
    CODE_AGENT_JIRA_TIMEOUT: int = int(os.getenv("CODE_AGENT_JIRA_TIMEOUT", "20"))
    CODE_AGENT_ANALYZE_BLOCKING: bool = os.getenv("CODE_AGENT_ANALYZE_BLOCKING", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # When > 0 and the deterministic gate passed, skip the verifier's LLM review
    # for modify-only changes whose total diff is at most this many characters.
    # 0 (default) always runs the LLM review.
    CODE_AGENT_VERIFIER_SKIP_LLM_TRIVIAL_CHARS: int = int(
        os.getenv("CODE_AGENT_VERIFIER_SKIP_LLM_TRIVIAL_CHARS", "0")
    )
    # Epic full-auto mode: default for the per-request auto_approve flag. When
    # true (or when a start-epic request passes auto_approve=true) the epic plan
    # is executed immediately without the human approval gate, and failed child
    # stories are retried automatically up to EPIC_CHILD_AUTO_RETRIES times.
    EPIC_AUTO_APPROVE: bool = os.getenv("EPIC_AUTO_APPROVE", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    EPIC_CHILD_AUTO_RETRIES: int = int(os.getenv("EPIC_CHILD_AUTO_RETRIES", "1"))
    # Default for single (non-epic) ticket runs: when true, a run approves its own
    # plan and proceeds to development without the human "Review & Approve" gate.
    # Overridable per request via the start-run auto_approve flag.
    CODE_AGENT_AUTO_APPROVE: bool = os.getenv("CODE_AGENT_AUTO_APPROVE", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    DART_BIN: str = os.getenv("DART_BIN", "dart")
    FLUTTER_BIN: str = os.getenv("FLUTTER_BIN", "flutter")
    CODE_AGENT_CORS_ORIGINS: str = os.getenv("CODE_AGENT_CORS_ORIGINS", "*")

    # Jira Cloud integration
    JIRA_BASE_URL: str | None = os.getenv("JIRA_BASE_URL")
    JIRA_USER_EMAIL: str | None = os.getenv("JIRA_USER_EMAIL")
    JIRA_API_TOKEN: str | None = os.getenv("JIRA_API_TOKEN")
    JIRA_ATTACHMENTS_DIR: str = os.path.abspath(
        os.getenv("JIRA_ATTACHMENTS_DIR", "./data/jira_attachments")
    )

    # PO Agent settings
    PO_INTAKE_ENABLED: bool = os.getenv("PO_INTAKE_ENABLED", "true").lower() in ("1", "true", "yes")
    PO_MAX_QUESTIONS: int = int(os.getenv("PO_MAX_QUESTIONS", "5"))
    PO_READINESS_THRESHOLD: float = float(os.getenv("PO_READINESS_THRESHOLD", "0.7"))
    PO_RESEARCH_DEPTH_DEFAULT: str = os.getenv("PO_RESEARCH_DEPTH_DEFAULT", "codebase")

    # Simulator view: local iOS Simulator mirror + WebDriverAgent (WDA) automation.
    # WDA_DIR is where the upstream Appium WebDriverAgent project is auto-cloned
    # on first use to bootstrap tap/hierarchy support.
    CODE_AGENT_WDA_DIR: str = os.path.abspath(
        os.getenv("CODE_AGENT_WDA_DIR", "./data/webdriveragent")
    )
    CODE_AGENT_WDA_PORT: int = int(os.getenv("CODE_AGENT_WDA_PORT", "8100"))
    CODE_AGENT_WDA_BOOTSTRAP_TIMEOUT: int = int(
        os.getenv("CODE_AGENT_WDA_BOOTSTRAP_TIMEOUT", "150")
    )

    # Dev loop runtime: "legacy" (hand-rolled tool-calling loop, any configured
    # provider) or "sdk" (Claude Agent SDK — owns context management,
    # compaction, and the turn loop itself; see app.agents.roles.dev_sdk).
    # Every other role (planner/verifier/qa/epic_planner/...) is unaffected —
    # this flag scopes to the dev node only.
    CODE_AGENT_DEV_RUNTIME: str = os.getenv("CODE_AGENT_DEV_RUNTIME", "legacy")
    # The SDK talks to Claude directly, not through the app's own multi-provider
    # llm_providers abstraction, so it needs its own key. python-dotenv's
    # load_dotenv() above already puts a .env-configured value into the process
    # environment, which is what a spawned Claude Code subprocess inherits —
    # this is also passed explicitly via ClaudeAgentOptions.env as a second,
    # more direct path that doesn't depend on env inheritance.
    ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
    CODE_AGENT_DEV_SDK_MODEL: str = os.getenv("CODE_AGENT_DEV_SDK_MODEL", "claude-opus-5")
    # low|medium|high|xhigh|max. xhigh is Anthropic's own starting point for
    # coding/agentic work; sweep down against measured runs from here.
    CODE_AGENT_DEV_SDK_EFFORT: str = os.getenv("CODE_AGENT_DEV_SDK_EFFORT", "xhigh")
    CODE_AGENT_DEV_SDK_MAX_TURNS: int = int(os.getenv("CODE_AGENT_DEV_SDK_MAX_TURNS", "40"))
    # 0 disables the budget cap (unset on ClaudeAgentOptions).
    CODE_AGENT_DEV_SDK_MAX_BUDGET_USD: float = float(
        os.getenv("CODE_AGENT_DEV_SDK_MAX_BUDGET_USD", "0")
    )

    # Planner runtime: "legacy" (hand-rolled explorer loop + one structured
    # chat completion) or "sdk" (Claude Agent SDK — one session does live
    # exploration and produces the schema-validated plan; see
    # app.agents.roles.planner_sdk). Independent of CODE_AGENT_DEV_RUNTIME —
    # each role's runtime is switched separately.
    CODE_AGENT_PLANNER_RUNTIME: str = os.getenv("CODE_AGENT_PLANNER_RUNTIME", "legacy")
    CODE_AGENT_PLANNER_SDK_MODEL: str = os.getenv("CODE_AGENT_PLANNER_SDK_MODEL", "claude-opus-5")
    CODE_AGENT_PLANNER_SDK_EFFORT: str = os.getenv("CODE_AGENT_PLANNER_SDK_EFFORT", "high")
    # 0 (the default) = UNBOUNDED: no max_turns is passed, so Claude Code runs
    # its own exploration loop until it decides the plan is done.
    #
    # A turn cap is the wrong guardrail for planning. Unlike the dev runtime —
    # where hitting the cap is a recoverable soft stop, the code is already on
    # disk, and the next iteration resumes the same session — a planner that
    # runs out of turns produces NOTHING: no plan, no partial artifact, and
    # every token spent exploring is wasted. Exploring a real codebase to
    # produce a file-accurate plan routinely needs more turns than any number
    # that looks reasonable in a config file (the original 20 was nowhere near
    # enough for a Flutter repo). Bound cost with
    # CODE_AGENT_PLANNER_SDK_MAX_BUDGET_USD instead, which fails on the thing
    # actually worth limiting.
    CODE_AGENT_PLANNER_SDK_MAX_TURNS: int = int(
        os.getenv("CODE_AGENT_PLANNER_SDK_MAX_TURNS", "0")
    )
    # 0 disables the budget cap. Only meaningful on API-key auth; a
    # subscription-authenticated run is limited by the subscription instead.
    CODE_AGENT_PLANNER_SDK_MAX_BUDGET_USD: float = float(
        os.getenv("CODE_AGENT_PLANNER_SDK_MAX_BUDGET_USD", "0")
    )
    # Retries for TRANSIENT planner failures only (dropped stream, provider
    # overload, rate limit) — see sdk_common.is_transient_sdk_error. Retrying
    # is safe here because planning is read-only: a fresh attempt duplicates
    # no work and leaves nothing behind. Non-transient errors never retry.
    # 0 disables retrying.
    CODE_AGENT_PLANNER_SDK_MAX_RETRIES: int = int(
        os.getenv("CODE_AGENT_PLANNER_SDK_MAX_RETRIES", "2")
    )


settings = Settings()
