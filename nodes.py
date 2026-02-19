"""LangGraph node functions for the ORD pipeline."""

from __future__ import annotations

import base64
import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config import (
    CIRCUIT_GENERATOR_TEMPS,
    GENERATOR_MODEL,
    INTENT_MODEL,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    QUESTION_MODEL,
    RAG_TOP_K,
)
from contracts import ERR_NO_ORD_CODE, ERR_SPACING_VIOLATION, STAGE_EXTRACTION
from models import IntentClassification, LayoutFixPlan
from prompts import (
    CIRCUIT_RETRY_PROMPT,
    GENERATOR_SYSTEM_PROMPT,
    INTENT_CLASSIFIER_PROMPT,
    LAYOUT_FIX_SYSTEM_PROMPT,
    QUESTION_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    RAG_GENERATION_PROMPT,
    SPACING_FIX_USER_PROMPT,
    STAGE_GUIDANCE,
)
from rag import get_vectorstore, query_similar_examples
from state import PipelineState
from validator import (
    ensure_parameter_defaults,
    ensure_version_header,
    extract_ord_code,
    fix_spacing_via_worker,
    strip_explicit_helpers,
    validate_ord_code_full,
)

logger = logging.getLogger(__name__)


def get_llm(
    model: str | None = None,
    temperature: float | None = None,
    model_kwargs: dict | None = None,
):
    """Create a chat LLM based on the configured provider.

    Args:
        model: OpenAI model name override. Ignored for Ollama.
        temperature: Temperature override.
        model_kwargs: Additional model kwargs (e.g., reasoning_effort).
    """
    temp = temperature if temperature is not None else LLM_TEMPERATURE
    if LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=temp,
        )
    return ChatOpenAI(
        model=model or LLM_MODEL,
        temperature=temp,
        model_kwargs=model_kwargs or {},
    )


def convert_history(history: list[dict]) -> list[HumanMessage | AIMessage]:
    """Convert Gradio history dicts to LangChain messages. Cap at last 10 pairs."""
    messages = []
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages[-20:]


def get_stage_guidance(stage: str) -> str:
    """Return stage-specific fix guidance for retry prompts."""
    return STAGE_GUIDANCE.get(stage, "")


def _state_temperature(state: PipelineState, default: float = 0.0) -> float:
    """Read temperature from pipeline state with safe fallback."""
    try:
        value = float(state.get("temperature", default))
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(2.0, value))


def _reasoning_kwargs(state: PipelineState, model: str | None) -> dict:
    """Return provider/model-safe reasoning kwargs."""
    if LLM_PROVIDER != "openai":
        return {}

    effort = state.get("reasoning_effort", "none")
    if effort not in {"low", "medium", "high"}:
        return {}

    model_name = str(model or LLM_MODEL)
    if not model_name.startswith("gpt-5"):
        return {}

    return {"reasoning_effort": effort}


def _fallback_intent_from_user_message(user_message: str) -> str:
    """Classify intent from user message when model fallback output is ambiguous."""
    text = user_message.lower()
    generation_signals = (
        "generate",
        "create",
        "build",
        "write",
        "design",
        "make",
        "modify",
        "update",
        "fix",
        "implement",
        "convert",
    )
    question_signals = (
        "what",
        "how",
        "why",
        "when",
        "where",
        "explain",
        "difference",
        "mean",
    )

    if any(signal in text for signal in generation_signals):
        return "generate"
    if "?" in text and any(signal in text for signal in question_signals):
        return "question"
    return "generate"


def intent_classifier(state: PipelineState) -> dict:
    """Classify user intent as 'generate' or 'question'."""
    print("\n" + "=" * 60)
    print(
        "[INTENT CLASSIFIER] Classifying: "
        f"{state['user_message'][:80]}... (model={INTENT_MODEL})"
    )
    llm = get_llm(model=INTENT_MODEL)
    try:
        structured_llm = llm.with_structured_output(IntentClassification)
        result = structured_llm.invoke([
            SystemMessage(content=INTENT_CLASSIFIER_PROMPT),
            HumanMessage(content=state["user_message"]),
        ])
        print(f"[INTENT CLASSIFIER] Result: {result.intent}")
        return {"intent": result.intent}
    except Exception as e:
        print(f"[INTENT CLASSIFIER] Structured output failed ({e}), using fallback")

        response = llm.invoke([
            SystemMessage(content=INTENT_CLASSIFIER_PROMPT),
            HumanMessage(content=state["user_message"]),
        ])
        intent = response.content.strip().lower()
        match = re.search(r"\b(generate|question)\b", intent)
        if match:
            classified = match.group(1)
        else:
            classified = _fallback_intent_from_user_message(state["user_message"])
            print("[INTENT CLASSIFIER] Ambiguous fallback response, using message heuristic")
        print(f"[INTENT CLASSIFIER] Fallback result: {classified}")
        return {"intent": classified}


def rag_retriever(state: PipelineState) -> dict:
    """Retrieve similar ORD examples via RAG."""
    print(
        f"\n[RAG] Retrieving top-{RAG_TOP_K} examples for: "
        f"{state['user_message'][:80]}..."
    )
    vectorstore = get_vectorstore()
    docs = query_similar_examples(vectorstore, state["user_message"], k=RAG_TOP_K)
    for doc in docs:
        print(f"  [RAG] Retrieved: {doc.metadata.get('filename', '?')}")

    examples_text = "\n\n---\n\n".join(
        f"**{doc.metadata.get('filename', 'example')}**:\n```ord\n{doc.page_content}\n```"
        for doc in docs
    )

    return {
        "retrieved_examples": examples_text,
        "retrieved_docs": [
            {"filename": d.metadata.get("filename"), "content": d.page_content}
            for d in docs
        ],
    }


def circuit_generator(state: PipelineState) -> dict:
    """Generate ORD circuit code with real positions using direct text output."""
    attempt = state.get("circuit_attempt", 0)
    base_temperature = CIRCUIT_GENERATOR_TEMPS[
        min(attempt, len(CIRCUIT_GENERATOR_TEMPS) - 1)
    ]
    ui_temperature = _state_temperature(state)
    temperature = min(2.0, base_temperature + ui_temperature)
    print(
        f"\n[GENERATOR] Attempt {attempt} "
        f"(model={GENERATOR_MODEL}, temperature={temperature})"
    )
    llm = get_llm(
        model=GENERATOR_MODEL,
        temperature=temperature,
        model_kwargs=_reasoning_kwargs(state, GENERATOR_MODEL),
    )

    if attempt == 0:
        history = convert_history(state.get("chat_history", []))
        user_prompt = RAG_GENERATION_PROMPT.format(
            retrieved_examples=state.get("retrieved_examples", ""),
            user_message=state["user_message"],
        )
        messages = [
            SystemMessage(content=GENERATOR_SYSTEM_PROMPT),
            *history,
            HumanMessage(content=user_prompt),
        ]
    else:
        messages = list(state.get("generator_messages", []))
        print(
            "  [GENERATOR] Retrying after "
            f"{state.get('circuit_error_stage', '?')} error"
        )
        retry_content = CIRCUIT_RETRY_PROMPT.format(
            error_stage=state.get("circuit_error_stage", "unknown"),
            error_message=state.get("circuit_error_message", "Unknown error"),
            previous_code=state.get("generated_code", ""),
            stage_guidance=get_stage_guidance(state.get("circuit_error_stage", "")),
        )
        messages.append(HumanMessage(content=retry_content))

    response = llm.invoke(messages)
    code = extract_ord_code(response.content)
    if code is None:
        code = ""
        print("  [GENERATOR] WARNING: No code extracted from response")
    else:
        code = ensure_version_header(code)
        code = ensure_parameter_defaults(code)
        print(f"  [GENERATOR] Code extracted ({len(code)} chars)")

    reasoning = response.content
    response_messages = messages + [AIMessage(content=response.content)]

    return {
        "generated_code": code,
        "generator_reasoning": reasoning,
        "generator_messages": response_messages,
    }


def circuit_validator(state: PipelineState) -> dict:
    """Validate generated ORD code fully (all 7 stages including spacing check)."""
    code = state.get("generated_code", "")
    print(f"\n[VALIDATOR] Validating {len(code)} chars (all 7 stages)...")
    if not code:
        print("  [VALIDATOR] FAIL: No code to validate")
        return {
            "circuit_validation_success": False,
            "circuit_error_stage": STAGE_EXTRACTION,
            "circuit_error_code": ERR_NO_ORD_CODE,
            "circuit_error_message": "No ORD code was generated.",
        }

    result = validate_ord_code_full(code)

    if result.success:
        svg_size = len(result.svg_bytes) if result.svg_bytes else 0
        print(f"  [VALIDATOR] PASS - SVG generated ({svg_size} bytes), cells: {result.cell_names}")
    else:
        print(
            "  [VALIDATOR] FAIL at "
            f"{result.error_stage}: {result.error_message[:120]}..."
        )

    return {
        "circuit_validation_success": result.success,
        "circuit_error_stage": result.error_stage,
        "circuit_error_code": result.error_code,
        "circuit_error_message": result.error_message,
        "cell_names": result.cell_names if result.cell_names else state.get("cell_names", []),
        "svg_bytes": result.svg_bytes,
    }


def layout_fixer(state: PipelineState) -> dict:
    """Fix spacing violations using structured output.

    Asks the LLM for a structured list of position changes to resolve
    bounding-box spacing violations, then applies them programmatically.
    Falls back to full code regeneration if structured fixing fails.
    """
    code = state.get("generated_code", "")
    feedback = state.get("circuit_error_message", "")
    print(f"\n[LAYOUT FIXER] Applying structured layout fixes (model={GENERATOR_MODEL})")
    print(f"  [LAYOUT FIXER] Feedback: {feedback[:120]}...")

    llm = get_llm(model=GENERATOR_MODEL, temperature=0)

    try:
        user_prompt = SPACING_FIX_USER_PROMPT.format(feedback=feedback, ord_code=code)

        structured_llm = llm.with_structured_output(LayoutFixPlan)
        plan = structured_llm.invoke([
            SystemMessage(content=LAYOUT_FIX_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])

        print(
            f"  [LAYOUT FIXER] Got {len(plan.changes)} changes: "
            f"{plan.reasoning[:80]}"
        )
        for change in plan.changes:
            parts = [f"{change.element_name}"]
            if change.new_pos_x is not None:
                parts.append(f"pos=({change.new_pos_x},{change.new_pos_y})")
            if change.new_alignment:
                parts.append(f"align={change.new_alignment}")
            if change.disable_route:
                parts.append("route=False")
            print(f"    {' '.join(parts)}")

        changes_dicts = [c.model_dump() for c in plan.changes]
        result = fix_spacing_via_worker(code, changes_dicts)

        if result.success:
            fixed_code = result.fixed_source or code
            print(
                f"  [LAYOUT FIXER] Object-level fix succeeded "
                f"({len(code)} -> {len(fixed_code)} chars)"
            )
            return {"generated_code": fixed_code, "svg_bytes": result.svg_bytes}

        if result.error_code == ERR_SPACING_VIOLATION:
            print(
                f"  [LAYOUT FIXER] Object-level fix still has violations, "
                f"falling back to full regeneration"
            )
            feedback = result.error_message
        else:
            print(
                f"  [LAYOUT FIXER] Object-level fix failed "
                f"({result.error_stage}: {result.error_message[:80]}), "
                f"falling back to full regeneration"
            )

    except Exception as e:
        print(f"  [LAYOUT FIXER] Structured fix failed ({e}), falling back to full regeneration")

    messages = list(state.get("generator_messages", []))
    messages.append(
        HumanMessage(
            content=CIRCUIT_RETRY_PROMPT.format(
                error_stage="spacing",
                error_message=feedback,
                previous_code=code,
                stage_guidance=get_stage_guidance("spacing"),
            )
        )
    )

    temperature = CIRCUIT_GENERATOR_TEMPS[
        min(state.get("circuit_attempt", 0), len(CIRCUIT_GENERATOR_TEMPS) - 1)
    ]
    fallback_temperature = min(2.0, temperature + _state_temperature(state))
    fallback_llm = get_llm(
        model=GENERATOR_MODEL,
        temperature=fallback_temperature,
        model_kwargs=_reasoning_kwargs(state, GENERATOR_MODEL),
    )

    response = fallback_llm.invoke(messages)
    fallback_code = extract_ord_code(response.content)

    if fallback_code:
        fallback_code = ensure_version_header(fallback_code)
        fallback_code = ensure_parameter_defaults(fallback_code)
        print(f"  [LAYOUT FIXER] Fallback produced code ({len(fallback_code)} chars)")
    else:
        fallback_code = code
        print("  [LAYOUT FIXER] Fallback produced no code, keeping original")

    return {
        "generated_code": fallback_code,
        "generator_messages": messages + [AIMessage(content=response.content)],
    }


def question_handler(state: PipelineState) -> dict:
    """Handle a general question with RAG context."""
    print(f"\n[QUESTION HANDLER] Answering question... (model={QUESTION_MODEL})")
    llm = get_llm(
        model=QUESTION_MODEL,
        temperature=_state_temperature(state),
        model_kwargs=_reasoning_kwargs(state, QUESTION_MODEL),
    )

    history = convert_history(state.get("chat_history", []))

    context = ""
    if state.get("retrieved_examples"):
        context = (
            "Here are some relevant ORD examples for context:\n\n"
            f"{state['retrieved_examples']}\n\n---\n"
        )

    user_prompt = QUESTION_PROMPT.format(
        retrieved_context=context,
        user_message=state["user_message"],
    )

    messages = [
        SystemMessage(content=QUESTION_SYSTEM_PROMPT),
        *history,
        HumanMessage(content=user_prompt),
    ]

    response = llm.invoke(messages)
    print(f"  [QUESTION HANDLER] Response generated ({len(response.content)} chars)")
    return {"question_response": response.content}


def _strip_code_fences(text: str) -> str:
    """Remove any code fences from reasoning text to prevent fragmented output."""
    import re

    text = re.sub(r"```(?:ord|python)\s*\n.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"```\s*\n.*?```", "", text, flags=re.DOTALL)
    return text.strip()


def format_response(state: PipelineState) -> dict:
    """Assemble the final user-facing response.

    Always outputs exactly ONE code block for generation results.
    When validation succeeded, strips explicit helper calls and re-renders
    to produce clean output code with an updated SVG.
    """

    print("\n[FORMAT RESPONSE] Assembling final response...")
    if state.get("intent") == "question":
        print("  [FORMAT RESPONSE] Question path")
        return {"final_response": state.get("question_response", "")}

    code = state.get("generated_code", "")
    svg_bytes = state.get("svg_bytes")

    if code and state.get("circuit_validation_success"):
        stripped = strip_explicit_helpers(code)
        print(f"  [FORMAT RESPONSE] Stripped helpers ({len(code)} -> {len(stripped)} chars)")
        rerender_result = validate_ord_code_full(stripped)
        if rerender_result.success and rerender_result.svg_bytes:
            code = stripped
            svg_bytes = rerender_result.svg_bytes
            print("  [FORMAT RESPONSE] Re-render of stripped code OK")
        else:
            print(
                "  [FORMAT RESPONSE] Re-render failed ("
                f"{rerender_result.error_stage}: {rerender_result.error_message[:80]}), keeping original code"
            )

    parts = []

    reasoning = state.get("generator_reasoning", "")
    if reasoning:
        clean_reasoning = _strip_code_fences(reasoning)
        if clean_reasoning:
            parts.append(clean_reasoning)

    if code:
        parts.append(f"```ord\n{code}\n```")
        print(f"  [FORMAT RESPONSE] Code block included ({len(code)} chars)")

    if svg_bytes and state.get("circuit_validation_success"):
        svg_b64 = base64.b64encode(svg_bytes).decode("ascii")
        parts.append(
            "**Circuit Preview:**\n\n<img src=\"data:image/svg+xml;base64,"
            f"{svg_b64}"
            "\" alt=\"Circuit schematic\" style=\"max-width:100%; background:white; padding:8px;\">"
        )
        print("  [FORMAT RESPONSE] SVG preview included")

    if not state.get("circuit_validation_success", False):
        error_stage = state.get("circuit_error_stage", "")
        error_code = state.get("circuit_error_code", "")
        error_msg = state.get("circuit_error_message", "")
        if error_stage:
            label = f"{error_stage} ({error_code})" if error_code else error_stage
            parts.append(f"**Validation failed during {label}:**\n```\n{error_msg}\n```")
            print(f"  [FORMAT RESPONSE] Validation error included ({label})")

    print("  [FORMAT RESPONSE] Done")
    print(f"{'=' * 60}\n")
    return {"final_response": "\n\n".join(parts)}


def increment_circuit_attempt(state: PipelineState) -> dict:
    """Increment the circuit generation attempt counter."""
    new_attempt = state.get("circuit_attempt", 0) + 1
    print(f"\n[RETRY] Circuit attempt -> {new_attempt}")
    return {"circuit_attempt": new_attempt}


def increment_spacing_attempt(state: PipelineState) -> dict:
    """Increment the spacing retry counter."""
    new_attempt = state.get("spacing_attempt", 0) + 1
    print(f"\n[RETRY] Spacing attempt -> {new_attempt}")
    return {"spacing_attempt": new_attempt}
