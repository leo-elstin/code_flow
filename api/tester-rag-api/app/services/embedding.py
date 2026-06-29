from openai import AsyncOpenAI

from app.core.config import settings

_openai_client: AsyncOpenAI | None = None


def get_embedding_dimensions() -> int:
    return 1536


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


async def get_embeddings(
    texts: list[str],
    *,
    task_type: str = "RETRIEVAL_QUERY",
    titles: list[str] | None = None,
) -> list[list[float]]:
    """Convert text into embedding vectors for semantic search."""
    del task_type, titles
    if not texts:
        return []

    response = await _get_openai_client().embeddings.create(
        input=texts,
        model=settings.OPENAI_EMBEDDING_MODEL,
    )
    return [item.embedding for item in response.data]
