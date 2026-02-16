# ORD Assistant

RAG-assisted LLM chatbot for generating and validating [ORD](https://github.com/tub-msc/ordec) circuit descriptions. Generates ORD code from natural language, validates it through a 6-stage pipeline (parse, compile, execute, discover, instantiate, render), and displays the schematic directly in the chat.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a `.env` file

Create a `.env` file in the project root (`ord_llm/.env`) with your API tokens:

```env
OPENAI_API_KEY=sk-...
HUGGINGFACE_API_KEY=hf_...
```

- **`OPENAI_API_KEY`** — Required for the LLM (GPT-4.1-mini by default) and OpenAI embeddings.
- **`HUGGINGFACE_API_KEY`** — Required for local HuggingFace sentence-transformer embeddings (used when `USE_OPENAI_EMBEDDINGS = False` in `config.py`).

### 3. Run

```bash
python app.py
```

This launches a Gradio web UI at `http://localhost:7860`.

## Configuration

Edit `config.py` to change:

| Setting | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `"openai"` | `"openai"` or `"ollama"` |
| `LLM_MODEL` | `"gpt-4.1-mini"` | OpenAI model name |
| `OLLAMA_MODEL` | `"gemma3"` | Ollama model name |
| `USE_OPENAI_EMBEDDINGS` | `True` | `True` for OpenAI embeddings, `False` for local HuggingFace |
| `RAG_TOP_K` | `3` | Number of examples retrieved per query |
| `MAX_RETRIES` | `3` | Validation retry attempts before returning an error |

## Project Structure

```
ord_llm/
  app.py            # Gradio chat UI
  agents.py         # LLM agent with retry loop
  prompts.py        # ORD language reference system prompt
  rag.py            # ChromaDB vectorstore over ORD examples
  validator.py      # 6-stage ORD validation pipeline
  config.py         # Settings and paths
  ord2_examples/    # ORD example files (used for RAG retrieval)
```
