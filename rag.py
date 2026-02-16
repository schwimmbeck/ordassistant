import re
from pathlib import Path

from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

from config import (
    CHROMA_PERSIST_DIR,
    EMBEDDING_MODEL,
    EXAMPLES_DIR,
    OPENAI_EMBEDDING_MODEL,
    RAG_TOP_K,
    USE_OPENAI_EMBEDDINGS,
)


def get_embeddings():
    """Return embedding model based on USE_OPENAI_EMBEDDINGS config."""
    if USE_OPENAI_EMBEDDINGS:
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL)
    else:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)


def load_ord_examples(examples_dir: Path | None = None) -> list[Document]:
    """Load all .ord files from the examples directory as LangChain Documents."""
    examples_dir = examples_dir or EXAMPLES_DIR
    documents = []
    for filepath in sorted(examples_dir.glob("*.ord")):
        content = filepath.read_text()
        # Extract cell names from the file
        cell_names = re.findall(r"^cell\s+(\w+)\s*:", content, re.MULTILINE)
        documents.append(
            Document(
                page_content=content,
                metadata={
                    "filename": filepath.name,
                    "cell_names": ", ".join(cell_names),
                },
            )
        )
    return documents


def build_vectorstore(force_rebuild: bool = False) -> Chroma:
    """Build or load the persisted ChromaDB vectorstore."""
    embeddings = get_embeddings()
    persist_path = Path(CHROMA_PERSIST_DIR)

    if not force_rebuild and persist_path.exists():
        try:
            vectorstore = Chroma(
                persist_directory=CHROMA_PERSIST_DIR,
                embedding_function=embeddings,
                collection_name="ord_examples",
            )
            if vectorstore._collection.count() > 0:
                return vectorstore
        except Exception:
            pass

    # Clear any stale data to avoid duplicates
    if persist_path.exists():
        import shutil
        shutil.rmtree(persist_path)

    documents = load_ord_examples()
    vectorstore = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory=CHROMA_PERSIST_DIR,
        collection_name="ord_examples",
    )
    return vectorstore


def query_similar_examples(
    vectorstore: Chroma, query: str, k: int = RAG_TOP_K
) -> list[Document]:
    """Return the top-k most similar ORD examples for a query."""
    return vectorstore.similarity_search(query, k=k)
