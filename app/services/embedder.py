from openai import OpenAI

from app.config import Settings
from app.services.usage import record_embedding_usage


class OpenAIEmbedder:
    def __init__(self, settings: Settings, db=None, operation: str = "embedding"):
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self.settings = settings
        self.db = db
        self.operation = operation
        self.client = OpenAI(api_key=settings.openai_api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.settings.embedding_batch_size):
            response = self.client.embeddings.create(
                model=self.settings.openai_embedding_model,
                input=texts[start:start + self.settings.embedding_batch_size],
                dimensions=self.settings.embedding_dimensions,
            )
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
            record_embedding_usage(self.db, self.settings, self.settings.openai_embedding_model,
                                   int(getattr(getattr(response, "usage", None), "prompt_tokens", 0) or 0), self.operation)
        return vectors