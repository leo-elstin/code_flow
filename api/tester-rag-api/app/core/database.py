from qdrant_client import QdrantClient
import os

# You can use an in-memory client for testing or a local path for disk persistence
client = QdrantClient(path="./qdrant_data")

def get_qdrant_client():
    return client