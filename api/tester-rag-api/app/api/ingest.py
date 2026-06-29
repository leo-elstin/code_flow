import os
import asyncio
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.agents.analyst import CodeAnalyst
from app.core.config import settings
from app.services.embedding import get_embeddings
from app.services.vector_store import upsert_to_qdrant, ensure_collection

router = APIRouter()


class IngestRequest(BaseModel):
    project_path: str


async def run_ingestion(project_path: str):
    print(f"Starting ingestion for: {project_path}")
    analyst = CodeAnalyst()
    all_chunks = []

    files_to_parse = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in (
            '.venv', '__pycache__', 'node_modules', '.dart_tool', 'build',
            'ios', 'android', 'windows', 'linux', 'macos', 'web'
        )]
        for file in files:
            if file.endswith((".dart", ".py")):
                files_to_parse.append(os.path.join(root, file))

    print(f"Found {len(files_to_parse)} files. Parsing...")

    async def parse_and_collect(file_path):
        chunks = await asyncio.to_thread(analyst.chunk_file, file_path)
        return chunks

    parsing_results = await asyncio.gather(*(parse_and_collect(f) for f in files_to_parse))
    for chunks in parsing_results:
        if chunks:
            all_chunks.extend(chunks)

    print(f"Extraction complete. Found {len(all_chunks)} chunks.")

    failed_batches = 0
    if all_chunks:
        batch_size = settings.INGEST_EMBEDDING_BATCH_SIZE
        concurrent_limit = settings.INGEST_EMBEDDING_CONCURRENCY
        semaphore = asyncio.Semaphore(concurrent_limit)

        batches = [all_chunks[i:i + batch_size] for i in range(0, len(all_chunks), batch_size)]
        print(
            f"Generating embeddings for {len(all_chunks)} chunks in {len(batches)} batches "
            f"(batch_size={batch_size}, concurrency={concurrent_limit})..."
        )

        async def process_batch(batch_chunks, batch_idx):
            nonlocal failed_batches
            async with semaphore:
                print(f"Processing batch {batch_idx + 1}/{len(batches)}...")
                texts = [c["content"] for c in batch_chunks]
                titles = [c["metadata"].get("file_path") for c in batch_chunks]
                try:
                    vectors = await get_embeddings(
                        texts,
                        task_type="RETRIEVAL_DOCUMENT",
                        titles=titles,
                    )
                    if len(vectors) != len(batch_chunks):
                        raise ValueError(
                            f"Expected {len(batch_chunks)} embeddings, got {len(vectors)}"
                        )
                    for chunk, vector in zip(batch_chunks, vectors):
                        chunk["vector"] = vector
                except Exception as e:
                    failed_batches += 1
                    print(f"Error in batch {batch_idx + 1}: {e}")

        await asyncio.gather(*(process_batch(b, i) for i, b in enumerate(batches)))

    if all_chunks:
        valid_chunks = [c for c in all_chunks if "vector" in c]
        if valid_chunks:
            print(f"Upserting {len(valid_chunks)} chunks to Qdrant...")
            ensure_collection("flutter_codebase")
            upsert_to_qdrant("flutter_codebase", valid_chunks)
            print(
                f"Ingestion complete! Embedded {len(valid_chunks)}/{len(all_chunks)} chunks. "
                f"Failed batches: {failed_batches}."
            )
        else:
            print(f"No valid embeddings were generated. Failed batches: {failed_batches}.")
    else:
        print("No chunks found to ingest.")


@router.post("/ingest")
async def ingest_codebase(request: IngestRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ingestion, request.project_path)
    return {"status": "Processing started"}
