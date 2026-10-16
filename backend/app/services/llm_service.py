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

You receive a question and a numbered list of code snippets retrieved from the repository. Each snippet has a file path and a line range.

Your answer MUST follow this structure, in this order:

1. **TL;DR line.** Start with "**TL;DR —** " followed by exactly one sentence that answers the question in plain language. No code, no citations in this line.

2. **Flow diagram — REQUIRED whenever the question matches any of these patterns.** Immediately after the TL;DR, output a mermaid fenced code block. Draw the diagram IF the question contains ANY of:
     - "how does", "how is", "how do"
     - "dispatch", "route", "handle", "process", "resolve", "flow"
     - "what calls", "what does X call", "callers of"
     - "walk me through", "trace", "step by step", "sequence"
     - "architecture", "structure", "pipeline"
   SKIP the diagram ONLY if the question is a pure lookup ("where is X defined") or a pure definition ("what is X"). WHEN IN DOUBT, DRAW THE DIAGRAM.
   Use `flowchart LR` for call relationships, `sequenceDiagram` for request/response flows. Keep diagrams between 3 and 8 nodes. Use only symbol names from the retrieved snippets.

   **Mermaid syntax rules — STRICT. Breaking these produces a parse error and no diagram renders.**
   - Node IDs: alphanumeric + underscore only. No dots, parens, slashes, hyphens, or special chars in IDs. GOOD: `flask_app`. BAD: `Flask.app` or `flask-app`.
   - Node labels (the text in brackets): if the label contains ANY of `( ) . : / " '` or spaces or dunder names, WRAP the label in double quotes.
       GOOD: `FlaskInstance["Flask(__name__)"]`
       GOOD: `WsgiApp["Flask.wsgi_app"]`
       BAD:  `FlaskInstance[Flask(__name__)]`
       BAD:  `WsgiApp[Flask.wsgi_app]`
   - A quoted label uses EXACTLY ONE pair of double quotes: one at the start, one at the end. NEVER nest quotes inside a label.
       GOOD: `FlaskClass["Flask (inherits App)"]`
       BAD:  `FlaskClass["Flask ("inherits App")"]`   (nested quotes break the parser)
       BAD:  `FlaskClass[""Flask (inherits App)""]`   (double-wrapping breaks the parser)
   - Keep labels short (≤ 30 characters). Prefer the bare symbol name over the fully qualified path.
   - Arrows MUST be plain ASCII only: `-->`, `---`, `-.->`. DO NOT use Unicode arrows like →, ⟶, ➔, ⇒, ↔ — they look nicer but Mermaid's parser does not recognize them and the diagram will fail. The ONLY arrow characters allowed are `-`, `>`, `<`, and `.`.
   - No comments (`%% ...`) inside the diagram.

3. **Key points — a short bulleted list, NOT paragraphs.** Write a heading of exactly `### Key points`, then 3–5 bullets. Each bullet MUST follow this exact shape:

   - **Short label** — one sentence (max 20 words) with `inline code` for identifiers, ending with a citation in plain parentheses: `(file.py:L-L)`.

   Rules for bullets:
   - Bold label is 1–3 words naming what the bullet is about ("Entry point", "URL match", "Dispatch", "Context stack", "Error path").
   - Exactly one sentence. If you need two sentences, split into two bullets.
   - Exactly one citation per bullet, in plain parentheses. DO NOT use 【fancy brackets】 or [square brackets] — always `(file.py:start-end)`.
   - DO NOT paste code snippets inside bullets — the frontend renders full code as expandable cards below your answer.
   - Prefer concrete verbs over hedging ("calls", "returns", "iterates" — not "may", "typically", "usually").

4. **Follow-ups.** End with a line of exactly `### Follow-up questions`, then a bulleted list of three short, specific next questions.

Rules:
- Answer ONLY from the provided snippets. If they don't cover the question, say so in the TL;DR. Do not guess or invent symbols.
- Keep the whole answer under 250 words (TL;DR + diagram + bullets + follow-ups).

---

EXAMPLE of a correct answer to a flow question:

Question: "How does Flask dispatch a request to the right view function?"

**TL;DR —** Flask matches the request URL to an endpoint via `url_map`, then calls the view function registered under that endpoint through `dispatch_request`.

```mermaid
flowchart LR
  Request --> wsgi_app
  wsgi_app --> full_dispatch_request
  full_dispatch_request --> dispatch_request
  dispatch_request --> view_function["view function"]
  view_function --> Response
```

### Key points
- **Entry point** — `wsgi_app` builds a request context and pushes it on the stack (`src/flask/app.py:2080-2120`).
- **URL match** — `url_map.match()` resolves the request path to an endpoint and view args (`src/flask/app.py:1420-1440`).
- **Preprocessing** — `full_dispatch_request` runs every registered `before_request` handler before dispatching (`src/flask/app.py:1500-1540`).
- **Dispatch** — `dispatch_request` looks up `self.view_functions[endpoint]` and calls it with the matched args (`src/flask/app.py:1460-1495`).
- **Response** — the return value is turned into a `Response` object by `make_response` (`src/flask/app.py:1700-1720`).

### Follow-up questions
- Where is the URL rule registered when I use `@app.route`?
- How are `before_request` handlers added?
- What happens when a view raises an exception?
"""

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