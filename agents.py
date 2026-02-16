from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config import (
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    MAX_RETRIES,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    RAG_TOP_K,
)
from prompts import ORD_SYSTEM_PROMPT, QUESTION_PROMPT, RAG_GENERATION_PROMPT, RETRY_PROMPT
from rag import build_vectorstore, query_similar_examples
from validator import ValidationResult, extract_ord_code, validate_ord_code


class OrdAssistant:
    def __init__(self):
        self.llm = self._create_llm()
        self.vectorstore = build_vectorstore()

    def _create_llm(self, temperature=None, model_kwargs=None):
        """Create the chat LLM based on the configured provider."""
        temp = temperature if temperature is not None else LLM_TEMPERATURE
        if LLM_PROVIDER == "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=OLLAMA_MODEL,
                base_url=OLLAMA_BASE_URL,
                temperature=temp,
            )
        return ChatOpenAI(
            model=LLM_MODEL,
            temperature=temp,
            model_kwargs=model_kwargs or {},
        )

    def update_llm_settings(
        self,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ):
        """Update LLM temperature and reasoning effort at runtime."""
        temp = temperature if temperature is not None else self.llm.temperature
        model_kwargs: dict = {}
        if LLM_PROVIDER == "openai":
            if reasoning_effort is not None:
                model_kwargs["reasoning_effort"] = reasoning_effort
        self.llm = self._create_llm(temperature=temp, model_kwargs=model_kwargs)

    def _convert_history(self, history: list[dict]) -> list[HumanMessage | AIMessage]:
        """Convert Gradio history dicts to LangChain messages. Cap at last 10 pairs."""
        messages = []
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        # Keep last 20 messages (10 pairs)
        return messages[-20:]

    def classify_intent(self, user_message: str) -> str:
        """Classify whether the user wants code generation or has a question."""
        messages = [
            SystemMessage(
                content=(
                    "You are an intent classifier. Respond with exactly one word: "
                    "'generate' if the user wants ORD circuit code to be created/modified, "
                    "or 'question' if they are asking a question about ORD or circuits. "
                    "Respond with only that one word."
                )
            ),
            HumanMessage(content=user_message),
        ]
        response = self.llm.invoke(messages)
        intent = response.content.strip().lower()
        return "generate" if "generate" in intent else "question"

    def handle_question(self, message: str, history: list[dict]) -> str:
        """Handle a general question with optional RAG context."""
        docs = query_similar_examples(self.vectorstore, message, k=RAG_TOP_K)
        context = ""
        for doc in docs:
            print(doc)

        if docs:
            examples_text = "\n\n---\n\n".join(
                f"**{doc.metadata.get('filename', 'example')}**:\n```ord\n{doc.page_content}\n```"
                for doc in docs
            )
            context = f"Here are some relevant ORD examples for context:\n\n{examples_text}\n\n---\n"

        user_prompt = QUESTION_PROMPT.format(
            retrieved_context=context, user_message=message
        )
        lc_history = self._convert_history(history)
        messages = [SystemMessage(content=ORD_SYSTEM_PROMPT)] + lc_history + [HumanMessage(content=user_prompt)]
        response = self.llm.invoke(messages)
        return response.content

    def handle_generation(self, message: str, history: list[dict]) -> tuple[str, ValidationResult | None]:
        """Handle ORD code generation with RAG retrieval, validation, and retry loop.

        Returns (response_text, validation_result).
        """
        # Step 1: Retrieve similar examples
        docs = query_similar_examples(self.vectorstore, message, k=RAG_TOP_K)
        examples_text = "\n\n---\n\n".join(
            f"**{doc.metadata.get('filename', 'example')}**:\n```ord\n{doc.page_content}\n```"
            for doc in docs
        )
        for doc in docs:
            print(doc)

        # Step 2: Build prompt with RAG examples
        user_prompt = RAG_GENERATION_PROMPT.format(
            retrieved_examples=examples_text, user_message=message
        )
        lc_history = self._convert_history(history)
        messages = [SystemMessage(content=ORD_SYSTEM_PROMPT)] + lc_history + [HumanMessage(content=user_prompt)]

        # Step 3: Generate
        response = self.llm.invoke(messages)
        response_text = response.content

        # Step 4: Extract and validate with retry loop
        for attempt in range(MAX_RETRIES + 1):
            code = extract_ord_code(response_text)
            if code is None:
                if attempt == MAX_RETRIES:
                    return response_text, None
                # Ask LLM to provide code in proper fence
                messages = [
                    SystemMessage(content=ORD_SYSTEM_PROMPT),
                    HumanMessage(
                        content=(
                            "Your previous response did not contain ORD code in a "
                            "```ord code fence. Please provide the ORD code wrapped in "
                            "```ord fences with the `# -*- version: ord2 -*-` header."
                        )
                    ),
                ]
                response = self.llm.invoke(messages)
                response_text = response.content
                continue

            result = validate_ord_code(code)
            if result.success:
                return response_text, result

            if attempt == MAX_RETRIES:
                error_note = (
                    f"\n\n**Validation failed after {MAX_RETRIES + 1} attempts "
                    f"during {result.error_stage}:**\n\n"
                    f"```\n{result.error_message}\n```"
                )
                return response_text + error_note, result

            # Build retry prompt (no RAG examples to save tokens)
            retry_content = RETRY_PROMPT.format(
                error_stage=result.error_stage,
                error_message=result.error_message,
                previous_code=code,
            )
            messages = [
                SystemMessage(content=ORD_SYSTEM_PROMPT),
                HumanMessage(content=retry_content),
            ]
            response = self.llm.invoke(messages)
            response_text = response.content

        return response_text, None

    def process_message(self, message: str, history: list[dict]) -> tuple[str, ValidationResult | None]:
        """Main entry point. Classifies intent and dispatches.

        Returns (response_text, validation_result_or_none).
        """
        intent = self.classify_intent(message)
        if intent == "generate":
            return self.handle_generation(message, history)
        else:
            return self.handle_question(message, history), None
