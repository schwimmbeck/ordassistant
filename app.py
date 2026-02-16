import base64

import gradio as gr

from agents import OrdAssistant

assistant: OrdAssistant | None = None


def initialize():
    global assistant
    assistant = OrdAssistant()


def chat_handler(
    message: str,
    history: list[dict],
    temperature: float,
    reasoning_effort: str,
) -> str:
    if assistant is None:
        initialize()

    # Apply runtime LLM settings
    effort = None if reasoning_effort == "none" else reasoning_effort
    assistant.update_llm_settings(temperature=temperature, reasoning_effort=effort)

    response_text, result = assistant.process_message(message, history)

    # Embed SVG preview if validation produced one
    if result and result.success and result.svg_bytes:
        svg_b64 = base64.b64encode(result.svg_bytes).decode("ascii")
        svg_html = (
            f'\n\n**Circuit Preview:**\n\n'
            f'<img src="data:image/svg+xml;base64,{svg_b64}" '
            f'alt="Circuit schematic" style="max-width:100%; background:white; padding:8px;">'
        )
        response_text += svg_html

    return response_text


def main():
    initialize()
    demo = gr.ChatInterface(
        fn=chat_handler,
        title="ORD Circuit Generator",
        description="Generate and validate ORD circuit descriptions with RAG-assisted code generation.",
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
