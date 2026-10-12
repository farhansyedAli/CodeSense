import json
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer

from app.config import settings
from app.database import get_db
from app.services import llm_service
from app.services import repository_service as svc

router = APIRouter(tags=["query"])

INDEXES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "indexes"
_model_cache: dict[str, SentenceTransformer] = {}


def _load_index(repo_id: str):
    index_dir = INDEXES_DIR / repo_id
    if not index_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Repository has not been indexed yet.",
        )

    with open(index_dir / "chunks_full.json") as f:
        chunks = json.load(f)
    with open(index_dir / "metadata.json") as f:
        metadata = json.load(f)

    embeddings = np.load(index_dir / "embeddings.npz")["embeddings"]
    model_name = metadata["embedding_model"]

    if model_name not in _model_cache:
        _model_cache[model_name] = SentenceTransformer(model_name)
    model = _model_cache[model_name]

    return chunks, embeddings, model, metadata


@router.post("/query")
async def query(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    repo_id = payload.get("repo_id")
    question = payload.get("question", "").strip()

    if not repo_id or not question:
        raise HTTPException(
            status_code=400,
            detail="Both repo_id and question are required.",
        )

    repo = await svc.get_repository(db, repo_id)
    if not repo or repo.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="Repository is not ready to be queried.",
        )

    chunks, embeddings, model, _metadata = _load_index(repo_id)

    # Embed the query and retrieve the most similar chunks
    query_vec = model.encode([question], show_progress_bar=False)
    similarities = cosine_similarity(query_vec, embeddings)[0]
    top_indices = np.argsort(similarities)[::-1][: settings.llm_top_k]

    # Build the response payload (previews) and the LLM input (full code)
    results = []
    llm_chunks = []
    for idx in top_indices:
        chunk = chunks[idx]
        results.append({
            "score": float(similarities[idx]),
            "file": chunk["file_path"],
            "start_line": chunk["start_line"],
            "end_line": chunk["end_line"],
            "name": chunk["name"],
            "chunk_type": chunk["chunk_type"],
            "code_preview": chunk["code"][:400],
        })
        llm_chunks.append({
            "file": chunk["file_path"],
            "start_line": chunk["start_line"],
            "end_line": chunk["end_line"],
            "name": chunk["name"],
            "chunk_type": chunk["chunk_type"],
            "code": chunk["code"],
        })

    # Ask the LLM to answer from the retrieved chunks. generate_answer returns
    # None when the key is missing or the Groq call fails, in which case we
    # fall back to the deterministic template answer.
    answer = await llm_service.generate_answer(question, llm_chunks)
    answer_source = "llm"
    if answer is None:
        answer_source = "fallback"
        top = results[0]
        answer = (
            f"The most relevant code is in {top['file']} "
            f"(lines {top['start_line']}-{top['end_line']}), "
            f"in the {top['chunk_type']} `{top['name']}`."
        )

    return {
        "question": question,
        "answer": answer,
        "answer_source": answer_source,
        "citations": [
            {
                "file": r["file"],
                "start_line": r["start_line"],
                "end_line": r["end_line"],
                "name": r["name"],
                "score": r["score"],
            }
            for r in results
        ],
        "top_results": results,
    }