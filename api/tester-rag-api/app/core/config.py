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
    # rule ticket like OIPO-667) are fetched and their descriptions handed to the
    # planner, so it can plan without pausing to ask about them.
    CODE_AGENT_RESOLVE_REFERENCED_TICKETS: bool = os.getenv(
        "CODE_AGENT_RESOLVE_REFERENCED_TICKETS", "true"
    ).lower() in ("1", "true", "yes")
    CODE_AGENT_MAX_REFERENCED_TICKETS: int = int(
        os.getenv("CODE_AGENT_MAX_REFERENCED_TICKETS", "5")
    )
    CODE_AGENT_LSP_TIMEOUT: int = int(os.getenv("CODE_AGENT_LSP_TIMEOUT", "30"))
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


settings = Settings()
