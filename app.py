import logging

import gradio as gr

from graph import build_graph

logging.basicConfig(level=logging.INFO)

# Build the LangGraph pipeline once at module level
pipeline = build_graph()


def chat_handler(
    message: str,
    history: list[dict],
    temperature: float,
    reasoning_effort: str,
) -> str:
    # Invoke the LangGraph pipeline
    result = pipeline.invoke({
        "user_message": message,
        "chat_history": history,
        "intent": "",
        "retrieved_examples": "",
        "retrieved_docs": [],
        "generator_messages": [],
        "generated_code": "",
        "generator_reasoning": "",
        "cell_names": [],
        "circuit_validation_success": False,
        "circuit_error_stage": "",
        "circuit_error_message": "",
        "circuit_attempt": 0,
        "spacing_attempt": 0,
        "svg_bytes": None,
        "question_response": "",
        "final_response": "",
    })

    return result.get("final_response", "")


def main():
    demo = gr.ChatInterface(
        fn=chat_handler,
        title="ORD Circuit Generator",
        description="Multi-agent ORD circuit generator with RAG, validation, and spacing check.",
        additional_inputs=[
            gr.Slider(
                minimum=0.0,
                maximum=2.0,
                value=0.0,
                step=0.05,
                label="Temperature",
            ),
            gr.Dropdown(
                choices=["none", "low", "medium", "high"],
                value="none",
                label="Reasoning Effort",
            ),
        ],
    )
    demo.launch()


if __name__ == "__main__":
    main()
