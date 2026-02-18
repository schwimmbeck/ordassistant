"""LangGraph state schema for the ORD pipeline."""

from typing import Literal, TypedDict

from langchain_core.messages import BaseMessage


class PipelineState(TypedDict, total=False):
    user_message: str
    chat_history: list[dict]
    temperature: float
    reasoning_effort: Literal["none", "low", "medium", "high"]

    intent: Literal["generate", "question", ""]

    retrieved_examples: str
    retrieved_docs: list[dict]

    generator_messages: list[BaseMessage]
    generated_code: str
    generator_reasoning: str
    cell_names: list[str]

    circuit_validation_success: bool
    circuit_error_stage: str
    circuit_error_code: str
    circuit_error_message: str
    circuit_attempt: int
    spacing_attempt: int
    svg_bytes: bytes | None

    question_response: str

    final_response: str
