from fastapi import APIRouter
from pydantic import BaseModel
from app.services.embedding import get_embeddings
from app.services.vector_store import search_qdrant

router = APIRouter()

class QueryRequest(BaseModel):
    query: str
    limit: int = 3

@router.post("/query")
async def query_codebase(request: QueryRequest):
    """
    Takes a natural language query, converts it to an embedding,
    and searches the Qdrant vector store for relevant code snippets.
    """
    # 1. Generate embedding for the query
    # We pass a list with one item to our batch-capable get_embeddings
    vectors = await get_embeddings([request.query], task_type="CODE_RETRIEVAL_QUERY")
    if not vectors:
        return {"error": "Failed to generate embedding for query"}
    
    query_vector = vectors[0]

    # 2. Search Qdrant
    results = search_qdrant("flutter_codebase", query_vector, limit=request.limit)

    return {
        "query": request.query,
        "results": results
    }
