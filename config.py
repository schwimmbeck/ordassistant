from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (ord_llm/)
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

# LLM provider: "openai" or "ollama"
LLM_PROVIDER = "openai"

# Per-node model configuration (OpenAI)
# Use cheap models for simple tasks, strong models for critical ones
INTENT_MODEL = "gpt-4.1-mini"       # Trivial classification task
GENERATOR_MODEL = "gpt-5-mini"    # Critical: must understand ORD syntax + layout
QUESTION_MODEL = "gpt-4.1-nano"     # General Q&A

# Default fallback (used if per-node not specified)
LLM_MODEL = "gpt-4.1-mini"
LLM_TEMPERATURE = 0

# Ollama settings
OLLAMA_MODEL = "gemma3"
OLLAMA_BASE_URL = "http://localhost:11434"

# Embedding settings
# Set to True to use OpenAI text-embedding-3-large instead of local HuggingFace
USE_OPENAI_EMBEDDINGS = False
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"

# RAG settings
RAG_TOP_K = 3

# Retry settings
MAX_CIRCUIT_RETRIES = 3  # Attempts for generator (all 7 stages)
MAX_SPACING_RETRIES = 2  # Attempts for spacing violation fixes
VALIDATION_TIMEOUT_SECONDS = 45

# Temperature escalation for circuit generator retries
CIRCUIT_GENERATOR_TEMPS = [0.0, 0.3, 0.6]

# Paths
EXAMPLES_DIR = _PROJECT_ROOT / "ord2_examples"
CHROMA_PERSIST_DIR = str(_PROJECT_ROOT / ".chromadb")
