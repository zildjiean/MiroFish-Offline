"""
EmbeddingService — embeddings from a local model, an OpenAI-compatible API, or Ollama.

Selected with ``EMBEDDING_API_STYLE``:

``local``   in-process sentence-transformers model (no network, no Ollama). Used here
            because the configured LLM gateway serves chat models only — it has no
            ``/v1/embeddings`` deployment.
``openai``  ``POST /v1/embeddings`` against any OpenAI-compatible gateway.
``ollama``  ``POST /api/embed`` — the original upstream behaviour.
"""

import time
import logging
from typing import List, Optional

import requests

from ..config import Config

logger = logging.getLogger('mirofish.embedding')


class EmbeddingService:
    """Generate embeddings using an OpenAI-compatible endpoint (or Ollama)."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 30,
        api_key: Optional[str] = None,
        api_style: Optional[str] = None,
        dim: Optional[int] = None,
    ):
        self.model = model or Config.EMBEDDING_MODEL
        self.base_url = (base_url or Config.EMBEDDING_BASE_URL).rstrip('/')
        self.max_retries = max_retries
        self.timeout = timeout
        self.api_key = api_key or Config.EMBEDDING_API_KEY
        self.api_style = (api_style or Config.EMBEDDING_API_STYLE).lower()
        self.dim = dim or Config.EMBEDDING_DIM

        # E5-family models expect an instruction prefix ("query: "). dotenv strips the
        # trailing space from .env values, so re-add it — E5 quality drops without it.
        prefix = Config.EMBEDDING_TEXT_PREFIX
        if prefix and not prefix.endswith(' '):
            prefix += ' '
        self.text_prefix = prefix

        # Local model is loaded lazily on first use (torch import is slow).
        self._model = None
        self._embed_url = None

        if self.api_style == 'local':
            pass
        elif self.api_style == 'ollama':
            self._embed_url = f"{self.base_url}/api/embed"
        else:
            # Accept a base URL given either with or without the /v1 suffix.
            if self.base_url.endswith('/v1'):
                self._embed_url = f"{self.base_url}/embeddings"
            else:
                self._embed_url = f"{self.base_url}/v1/embeddings"

        # Simple in-memory cache (text -> embedding vector)
        # Using dict instead of lru_cache because lists aren't hashable
        self._cache: dict[str, List[float]] = {}
        self._cache_max_size = 2000

    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Input text to embed

        Returns:
            Float vector of length ``Config.EMBEDDING_DIM``

        Raises:
            EmbeddingError: If the request fails after retries
        """
        if not text or not text.strip():
            raise EmbeddingError("Cannot embed empty text")

        text = text.strip()

        # Check cache
        if text in self._cache:
            return self._cache[text]

        vectors = self._request_embeddings([text])
        vector = vectors[0]

        # Cache result
        self._cache_put(text, vector)

        return vector

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.

        Processes in batches to avoid overwhelming the provider.

        Args:
            texts: List of input texts
            batch_size: Number of texts per request

        Returns:
            List of embedding vectors (same order as input)
        """
        if not texts:
            return []

        results: List[Optional[List[float]]] = [None] * len(texts)
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []

        # Check cache first
        for i, text in enumerate(texts):
            text = text.strip() if text else ""
            if text in self._cache:
                results[i] = self._cache[text]
            elif text:
                uncached_indices.append(i)
                uncached_texts.append(text)
            else:
                # Empty text — zero vector
                results[i] = [0.0] * self.dim

        # Batch-embed uncached texts
        if uncached_texts:
            all_vectors: List[List[float]] = []
            for start in range(0, len(uncached_texts), batch_size):
                batch = uncached_texts[start:start + batch_size]
                vectors = self._request_embeddings(batch)
                all_vectors.extend(vectors)

            # Place results and cache
            for idx, vec, text in zip(uncached_indices, all_vectors, uncached_texts):
                results[idx] = vec
                self._cache_put(text, vec)

        return results  # type: ignore

    def _load_local_model(self):
        """Load the sentence-transformers model once, on first use."""
        if self._model is not None:
            return self._model

        import torch
        from sentence_transformers import SentenceTransformer

        # CPU-only box — cap threads so embedding does not starve the Flask workers.
        torch.set_num_threads(Config.EMBEDDING_TORCH_THREADS)

        logger.info(f"Loading local embedding model '{self.model}' (first call may download it)")
        self._model = SentenceTransformer(self.model, device='cpu')

        actual = self._model.get_sentence_embedding_dimension()
        if actual != self.dim:
            raise EmbeddingError(
                f"Model '{self.model}' produces {actual}-d vectors but EMBEDDING_DIM={self.dim}. "
                f"Set EMBEDDING_DIM={actual} in .env and rebuild the Neo4j vector indexes "
                f"(they are created with the dimension baked in)."
            )
        logger.info(f"Local embedding model ready ({actual} dimensions)")
        return self._model

    def _embed_local(self, texts: List[str]) -> List[List[float]]:
        """Encode texts with the in-process model."""
        model = self._load_local_model()
        prepared = [f"{self.text_prefix}{t}" for t in texts] if self.text_prefix else texts
        vectors = model.encode(
            prepared,
            normalize_embeddings=True,   # cosine similarity in Neo4j expects unit vectors
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]

    def _build_request(self, texts: List[str]):
        """Return (payload, headers) for the configured API style."""
        headers = {"Content-Type": "application/json"}

        if self.api_style == 'ollama':
            return {"model": self.model, "input": texts}, headers

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return {"model": self.model, "input": texts, "encoding_format": "float"}, headers

    def _parse_response(self, data: dict, expected: int) -> List[List[float]]:
        """Extract vectors from either wire format, preserving input order."""
        if self.api_style == 'ollama':
            embeddings = data.get("embeddings", [])
        else:
            # OpenAI format: {"data": [{"index": 0, "embedding": [...]}, ...]}
            items = data.get("data", [])
            items = sorted(items, key=lambda d: d.get("index", 0))
            embeddings = [item["embedding"] for item in items]

        if len(embeddings) != expected:
            raise EmbeddingError(
                f"Expected {expected} embeddings, got {len(embeddings)}"
            )
        return embeddings

    def _request_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        POST to the embeddings endpoint with retry.

        Args:
            texts: List of texts to embed (batched in a single request)

        Returns:
            List of embedding vectors
        """
        if self.api_style == 'local':
            return self._embed_local(texts)

        payload, headers = self._build_request(texts)

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self._embed_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return self._parse_response(response.json(), len(texts))

            except requests.exceptions.ConnectionError as e:
                last_error = e
                logger.warning(
                    f"Embedding connection failed (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
            except requests.exceptions.Timeout as e:
                last_error = e
                logger.warning(
                    f"Embedding request timed out (attempt {attempt + 1}/{self.max_retries})"
                )
            except requests.exceptions.HTTPError as e:
                last_error = e
                logger.error(
                    f"Embedding HTTP error: {e.response.status_code} - {e.response.text[:500]}"
                )
                if e.response.status_code >= 500:
                    # Server error — retry
                    pass
                else:
                    # Client error (4xx) — don't retry
                    raise EmbeddingError(f"Embedding request failed: {e}") from e
            except (KeyError, ValueError) as e:
                raise EmbeddingError(f"Invalid embedding response: {e}") from e

            # Exponential backoff
            if attempt < self.max_retries - 1:
                wait = 2 ** attempt
                logger.info(f"Retrying in {wait}s...")
                time.sleep(wait)

        raise EmbeddingError(
            f"Embedding failed after {self.max_retries} retries: {last_error}"
        )

    def _cache_put(self, text: str, vector: List[float]) -> None:
        """Add to cache, evicting oldest entries if full."""
        if len(self._cache) >= self._cache_max_size:
            # Remove ~10% of oldest entries
            keys_to_remove = list(self._cache.keys())[:self._cache_max_size // 10]
            for key in keys_to_remove:
                del self._cache[key]
        self._cache[text] = vector

    def health_check(self) -> bool:
        """Check the embedding backend is usable (loads the local model if needed)."""
        try:
            vec = self.embed("health check")
            return len(vec) > 0
        except Exception:
            return False


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass
