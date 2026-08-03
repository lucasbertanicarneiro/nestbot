"""Embeddings locais via sentence-transformers.

Por que local e nao API: o Groq nao expoe endpoint de embedding, e o
multilingual-e5-small tem qualidade boa em portugues, roda em CPU e
custa zero -- o que tira a ingestao do caminho do rate limit.

Detalhe do e5: o modelo foi treinado com prefixos assimetricos.
"query: " para a pergunta e "passage: " para o documento. Ignorar isso
degrada o recall de forma silenciosa.
"""
from __future__ import annotations

import logging
import threading

import numpy as np
from sentence_transformers import SentenceTransformer

from .config import config

log = logging.getLogger(__name__)

_modelo: SentenceTransformer | None = None
_lock = threading.Lock()


def obter_modelo() -> SentenceTransformer:
    """Carrega o modelo uma unica vez (thread-safe)."""
    global _modelo
    if _modelo is None:
        with _lock:
            if _modelo is None:
                log.info("Carregando modelo de embedding: %s", config.modelo_embedding)
                _modelo = SentenceTransformer(config.modelo_embedding)
                dim = _modelo.get_sentence_embedding_dimension()
                if dim != config.dimensao_embedding:
                    raise RuntimeError(
                        f"Dimensao do modelo ({dim}) difere do schema "
                        f"({config.dimensao_embedding}). Ajuste VECTOR(n) em db/init.sql."
                    )
    return _modelo


def embedar_passagens(textos: list[str]) -> np.ndarray:
    """Vetoriza chunks para indexacao."""
    modelo = obter_modelo()
    prefixados = [f"passage: {t}" for t in textos]
    return modelo.encode(
        prefixados,
        normalize_embeddings=True,  # normalizado -> cosine vira produto interno
        batch_size=32,
        show_progress_bar=len(textos) > 50,
    )


def embedar_consulta(texto: str) -> np.ndarray:
    """Vetoriza a pergunta do usuario."""
    modelo = obter_modelo()
    return modelo.encode(f"query: {texto}", normalize_embeddings=True)
