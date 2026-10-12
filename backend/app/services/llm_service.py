"""
LLM answer generation for CodeSense.

Takes the user's question plus the retrieved code chunks and asks Groq to
write a grounded, formatted answer. Every failure path returns None so the
caller can fall back to the deterministic template answer instead of
returning a 500 to the user.
"""

import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are CodeSense, an assistant that helps developers understand an unfamiliar Python codebase.

You will receive a question and a numbered list of code snippets retrieved from the repository. Each snippet has a file path and a line range.

Rules:
1. Answer ONLY using the provided snippets. Do not invent files, functions, or behaviour that the snippets do not show.
2. If the snippets do not contain enough information to answer, say so plainly and mention what the snippets do cover. Do not guess.
3. Cite sources inline as `path/to/file.py:START-END` right after each claim they support. Use only the paths and line ranges given in the snippets.
4. Be concise and direct. Start with the answer, then briefly explain how the code works.
5. Format with Markdown: short paragraphs or bullet points, `inline code` for identifiers, and fenced code blocks only for short excerpts that help the explanation. Do not paste whole snippets."""

_llm: Optional[ChatGroq] = None


def _get_llm() -> Optional[ChatGroq]:
    """Create the Groq client once and reuse it. Returns None if no API key is set."""
    global _llm
    if not settings.groq_api_key:
        return None
    if _llm is None:
        _llm = ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
        )
    return _llm


def build_context(chunks: list[dict[str, Any]]) -> str:
    """Format retrieved chunks into a numbered context block for the prompt."""
    limit = settings.llm_max_chunk_chars
    parts = []
    for i, c in enumerate(chunks, start=1):
        code = c.get("code", "") or ""
        if len(code) > limit:
            code = code[:limit] + "\n# ... (truncated)"
        parts.append(
            f"[{i}] {c['file']}:{c['start_line']}-{c['end_line']} "
            f"({c['chunk_type']} `{c['name']}`)\n"
            f"```python\n{code}\n```"
        )
    return "\n\n".join(parts)


def _extract_text(content: Any) -> str:
    """LangChain message content is usually a str, but can be a list of parts."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "".join(texts).strip()
    return ""


async def generate_answer(
    question: str, chunks: list[dict[str, Any]]
) -> Optional[str]:
    """
    Ask the LLM to answer `question` using `chunks`.

    Each chunk dict needs: file, start_line, end_line, name, chunk_type, code.
    Returns the answer text, or None if the LLM is unavailable or fails.
    """
    if not chunks:
        return None

    llm = _get_llm()
    if llm is None:
        logger.warning("GROQ_API_KEY is not set; using fallback answer.")
        return None

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Question: {question}\n\n"
                f"Retrieved code snippets:\n\n{build_context(chunks)}"
            )
        ),
    ]

    try:
        response = await llm.ainvoke(messages)
    except Exception:
        logger.exception("Groq call failed; using fallback answer.")
        return None

    answer = _extract_text(response.content)
    return answer or None