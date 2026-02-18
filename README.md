# ORD Assistant

Agentic ORD generation assistant with Gradio UI, LangGraph orchestration, RAG over `ord2_examples`, staged validation, spacing repair, and SVG preview.

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create `.env` in project root:

```env
OPENAI_API_KEY=sk-...
HUGGINGFACE_API_KEY=hf_...
```

3. Run:

```bash
python app.py
```

UI opens at `http://localhost:7860`.

## Runtime Flow

1. `intent_classifier` classifies `generate` vs `question`.
2. `rag_retriever` fetches top-k ORD examples.
3. `circuit_generator` creates ORD code (defaults injected for all `Parameter(...)`).
4. `circuit_validator` runs parse/compile/exec/discovery/instantiate/render/spacing.
5. On spacing errors, `layout_fixer` applies structured `.pos/.align/.route` edits.
6. `format_response` returns one ORD code block and SVG preview.

## Key Files

- `app.py`: Gradio entrypoint and pipeline invocation
- `graph.py`: LangGraph state machine and retry routing
- `nodes.py`: node implementations (intent/RAG/generation/validation/fix/format)
- `state.py`: `PipelineState` schema
- `models.py`: structured-output Pydantic models
- `contracts.py`: stage + error-code constants
- `validator.py`: validation engine + worker protocol + spacing checker
- `validator_worker.py`: subprocess worker for isolated validation
- `rag.py`: Chroma vectorstore loading/querying
- `prompts.py`: system/user prompts and stage guidance
- `ord2_examples/`: RAG corpus
- `evals/run_validator_eval.py`: baseline validator evaluation
- `tests/`: regression tests

## Configuration

In `config.py`:

- `LLM_PROVIDER` (`"openai"` or `"ollama"`)
- `INTENT_MODEL`, `GENERATOR_MODEL`, `QUESTION_MODEL`
- `RAG_TOP_K`
- `MAX_CIRCUIT_RETRIES`, `MAX_SPACING_RETRIES`
- `VALIDATION_TIMEOUT_SECONDS`
- `CIRCUIT_GENERATOR_TEMPS`

## Tests

Run all tests:

```bash
.venv/bin/python3.12 -m pytest
```

Run validator eval (default excludes `reg_*.ord` and `inverter_constraints.ord`):

```bash
.venv/bin/python3.12 evals/run_validator_eval.py --strict
```

Add custom excludes:

```bash
.venv/bin/python3.12 evals/run_validator_eval.py --exclude "sar_*.ord" --strict
```
