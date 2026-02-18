"""LangGraph state machine for the ORD pipeline."""

from langgraph.graph import END, StateGraph

from config import MAX_CIRCUIT_RETRIES, MAX_SPACING_RETRIES
from contracts import ERR_SPACING_VIOLATION
from nodes import (
    circuit_generator,
    circuit_validator,
    format_response,
    increment_circuit_attempt,
    increment_spacing_attempt,
    intent_classifier,
    layout_fixer,
    question_handler,
    rag_retriever,
)
from state import PipelineState


def route_after_intent(state: PipelineState) -> str:
    """Route to question handler or circuit generator based on intent."""
    if state.get("intent") == "question":
        return "question_handler"
    return "circuit_generator"


def route_after_circuit_validation(state: PipelineState) -> str:
    """Route after validation: to format response, spacing fix, or retry."""
    if state.get("circuit_validation_success"):
        return "format_response"

    if state.get("circuit_error_code") == ERR_SPACING_VIOLATION:
        spacing_attempt = state.get("spacing_attempt", 0)
        if spacing_attempt < MAX_SPACING_RETRIES:
            return "increment_spacing_attempt"
        return "format_response"

    attempt = state.get("circuit_attempt", 0)

    # `circuit_attempt` starts at 0. We can retry while attempt < MAX_CIRCUIT_RETRIES - 1.
    if attempt >= max(MAX_CIRCUIT_RETRIES - 1, 0):
        return "format_response"
    return "increment_circuit_attempt"


def build_graph():
    """Build and compile the ORD pipeline LangGraph."""
    graph = StateGraph(PipelineState)

    graph.add_node("intent_classifier", intent_classifier)
    graph.add_node("rag_retriever", rag_retriever)
    graph.add_node("circuit_generator", circuit_generator)
    graph.add_node("circuit_validator", circuit_validator)
    graph.add_node("layout_fixer", layout_fixer)
    graph.add_node("question_handler", question_handler)
    graph.add_node("format_response", format_response)
    graph.add_node("increment_circuit_attempt", increment_circuit_attempt)
    graph.add_node("increment_spacing_attempt", increment_spacing_attempt)

    graph.set_entry_point("intent_classifier")

    graph.add_edge("intent_classifier", "rag_retriever")

    graph.add_conditional_edges(
        "rag_retriever",
        route_after_intent,
        {
            "question_handler": "question_handler",
            "circuit_generator": "circuit_generator",
        },
    )

    graph.add_edge("question_handler", "format_response")

    graph.add_edge("circuit_generator", "circuit_validator")

    graph.add_conditional_edges(
        "circuit_validator",
        route_after_circuit_validation,
        {
            "format_response": "format_response",
            "increment_circuit_attempt": "increment_circuit_attempt",
            "increment_spacing_attempt": "increment_spacing_attempt",
        },
    )

    graph.add_edge("increment_circuit_attempt", "circuit_generator")

    graph.add_edge("increment_spacing_attempt", "layout_fixer")
    graph.add_edge("layout_fixer", "circuit_validator")

    graph.add_edge("format_response", END)

    return graph.compile()
