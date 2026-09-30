from fastapi import APIRouter
from indexing.graph_example import run_example

router = APIRouter(tags=["langgraph"])


@router.get("/langgraph-test")
async def test_langgraph(question: str = "Where is the authentication logic?"):
    """Run the LangGraph example on a test question."""
    result = run_example(question)
    return {
        "question": result["question"],
        "intent": result["intent"],
        "answer": result["answer"],
    }