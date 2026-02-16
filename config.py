import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (ord_llm/)
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

# LLM provider: "openai" or "ollama"
LLM_PROVIDER = "openai"

# OpenAI settings
LLM_MODEL = "gpt-4.1-mini"
LLM_TEMPERATURE = 0

# Ollama settings
OLLAMA_MODEL = "gemma3"
OLLAMA_BASE_URL = "http://localhost:11434"

# Embedding settings
# Set to True to use OpenAI text-embedding-3-large instead of local HuggingFace
USE_OPENAI_EMBEDDINGS = True
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"

# RAG settings
RAG_TOP_K = 3

# Validation settings
MAX_RETRIES = 3

# Paths
EXAMPLES_DIR = _PROJECT_ROOT / "ord2_examples"
CHROMA_PERSIST_DIR = str(_PROJECT_ROOT / ".chromadb")
