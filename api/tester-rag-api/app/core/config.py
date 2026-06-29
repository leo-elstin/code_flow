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
    CODE_AGENT_LSP_TIMEOUT: int = int(os.getenv("CODE_AGENT_LSP_TIMEOUT", "30"))
    CODE_AGENT_ANALYZE_BLOCKING: bool = os.getenv("CODE_AGENT_ANALYZE_BLOCKING", "true").lower() in (
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


settings = Settings()
