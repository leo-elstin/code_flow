from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance
import uuid

from app.services.embedding import get_embedding_dimensions

# Initialize the client once at the module level
client = QdrantClient("localhost", port=6333, timeout=10)


def ensure_collection(collection_name: str, vector_size: int | None = None):
    """Creates the collection if it doesn't exist."""
    size = vector_size or get_embedding_dimensions()
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=size, distance=Distance.COSINE),
        )


def upsert_to_qdrant(collection_name: str, chunks: list, batch_size: int = 100):
    """
    Takes processed chunks from CodeAnalyst and upserts to Qdrant in batches.
    """
    points = []
    for chunk in chunks:
        vector = chunk.get("vector")
        if vector is None:
            continue

        point_id = str(uuid.uuid4())
        # Store metadata AND the actual content so we can read it back
        payload = {**chunk["metadata"], "content": chunk["content"]}

        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload=payload
            )
        )

    # Perform batch upserts to avoid 'payload too large' errors
    print(f"Upserting {len(points)} points to Qdrant in batches of {batch_size}...")
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=collection_name, points=batch)


def search_qdrant(collection_name: str, query_vector: list, limit: int = 3):
    """
    Searches Qdrant for the most similar vectors.
    """
    try:
        # Check if collection exists first to avoid 404 error
        if not client.collection_exists(collection_name):
            print(f"Collection {collection_name} does not exist yet.")
            return []

        search_result = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=limit
        )
        
        results = []
        for point in search_result.points:
            results.append({
                "id": point.id,
                "score": point.score,
                "content": point.payload.get("content"),
                "metadata": {k: v for k, v in point.payload.items() if k != "content"}
            })
        return results
    except Exception as e:
        print(f"Error searching Qdrant: {e}")
        return []