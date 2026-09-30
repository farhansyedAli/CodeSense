"""
Minimal LangGraph two-agent example for CodeSense Sprint 0.

Proves that LangGraph is installed, configured, and runnable.
This is NOT the real Coordinator — it is a proof of concept.
"""

from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END


class AgentState(TypedDict):
    question: str
    intent: str
    answer: str


def classify_intent(state: AgentState) -> AgentState:
    """Router: decides which agent should handle the question."""
    question = state["question"].lower()
    if any(word in question for word in ["call", "caller", "impact", "break"]):
        intent = "impact"
    elif any(word in question for word in ["security", "vulnerab", "injection"]):
        intent = "security"
    else:
        intent = "semantic"
    return {**state, "intent": intent}


def semantic_agent(state: AgentState) -> AgentState:
    return {
        **state,
        "answer": f"[semantic] Would retrieve code chunks and answer: {state['question']}",
    }


def impact_agent(state: AgentState) -> AgentState:
    return {
        **state,
        "answer": f"[impact] Would traverse graph for: {state['question']}",
    }


def security_agent(state: AgentState) -> AgentState:
    return {
        **state,
        "answer": f"[security] Would run reachability scan for: {state['question']}",
    }


def route_by_intent(state: AgentState) -> Literal["semantic", "impact", "security"]:
    return state["intent"]


def build_graph():
    """Build the two-agent workflow graph."""
    workflow = StateGraph(AgentState)

    workflow.add_node("classify", classify_intent)
    workflow.add_node("semantic", semantic_agent)
    workflow.add_node("impact", impact_agent)
    workflow.add_node("security", security_agent)

    workflow.set_entry_point("classify")
    workflow.add_conditional_edges("classify", route_by_intent)
    workflow.add_edge("semantic", END)
    workflow.add_edge("impact", END)
    workflow.add_edge("security", END)

    return workflow.compile()


def run_example(question: str) -> dict:
    """Run the compiled graph on a single question."""
    graph = build_graph()
    result = graph.invoke({
        "question": question,
        "intent": "",
        "answer": "",
    })
    return result