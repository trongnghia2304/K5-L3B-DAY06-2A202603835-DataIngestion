"""Real embedding provider: a local Sentence Transformers model (no API key, no cost)."""
from typing import Protocol


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Sentence Transformers adapter. Vectors are L2-normalized so cosine distance
    in pgvector equals 1 - dot product. This model uses no query/document prefixes,
    so queries and documents are encoded the same way. Any load or encode failure
    raises; there is no fallback to mock vectors."""

    def __init__(self, model_name: str, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name, device="cpu")
        get_dims = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        dims = get_dims()
        if not dims:
            raise RuntimeError(f"Could not determine embedding dimensions for {model_name}")
        self.dimensions = int(dims)
        self.max_seq_length = int(self.model.max_seq_length)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        return vectors.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts) if texts else []

    def embed_query(self, question: str) -> list[float]:
        return self._encode([question])[0]

    def count_truncated(self, texts: list[str]) -> int:
        """How many texts exceed the model's max_seq_length and get cut when embedded."""
        lengths = [len(ids) for ids in self.model.tokenizer(texts, add_special_tokens=True)["input_ids"]]
        return sum(n > self.max_seq_length for n in lengths)
