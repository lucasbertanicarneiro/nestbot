"""Avaliacao automatica das respostas (LLM-as-judge).

Metricas no espirito do RAGAS, mas implementadas direto com o Groq para
manter uma dependencia a menos e caber no free tier:

  faithfulness        -> a resposta se sustenta no contexto recuperado?
  relevancia_resposta -> a resposta responde a pergunta?
  relevancia_contexto -> os chunks recuperados eram pertinentes?

Regra pratica da literatura que guia a leitura do dashboard: faithfulness
baixa quase sempre e problema de RECUPERACAO, nao de geracao. Se a nota cair,
o lugar de mexer e o chunking / a busca -- nao o prompt de geracao.

Roda em thread separada para nao segurar a resposta do bot.
"""
from __future__ import annotations

import logging
import threading

from . import db, prompts
from .config import config
from .llm import gerar_json
from .retrieval import ChunkRecuperado

log = logging.getLogger(__name__)

LIMIAR_FAITHFULNESS = 0.7


def _normalizar(valor) -> float | None:
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, n))


def avaliar(
    interacao_id: int,
    pergunta: str,
    resposta: str,
    chunks: list[ChunkRecuperado],
) -> dict | None:
    """Julga uma interacao e grava o resultado."""
    if not chunks or not resposta:
        return None

    contexto = "\n\n".join(f"[{c.documento}] {c.conteudo}" for c in chunks)

    try:
        bruto = gerar_json(
            sistema=prompts.AVALIADOR_SISTEMA,
            usuario=prompts.AVALIADOR_USUARIO.format(
                pergunta=pergunta, contexto=contexto, resposta=resposta
            ),
            max_tokens=400,
        )
    except Exception:
        log.exception("Falha ao avaliar interacao %s", interacao_id)
        return None

    avaliacao = {
        "faithfulness": _normalizar(bruto.get("faithfulness")),
        "relevancia_resposta": _normalizar(bruto.get("relevancia_resposta")),
        "relevancia_contexto": _normalizar(bruto.get("relevancia_contexto")),
        "justificativa": (bruto.get("justificativa") or "")[:500],
        "modelo_avaliador": config.modelo_rapido,
    }

    db.registrar_avaliacao(interacao_id, avaliacao)

    # Faithfulness baixa vira item de backlog automaticamente.
    f = avaliacao["faithfulness"]
    if f is not None and f < LIMIAR_FAITHFULNESS:
        db.registrar_lacuna(interacao_id, pergunta, "faithfulness_baixa")
        log.warning("Faithfulness baixa (%.2f) na interacao %s", f, interacao_id)

    return avaliacao


def avaliar_em_background(
    interacao_id: int,
    pergunta: str,
    resposta: str,
    chunks: list[ChunkRecuperado],
) -> None:
    """Dispara a avaliacao sem bloquear a resposta ao usuario."""
    if not config.avaliar_automaticamente:
        return

    thread = threading.Thread(
        target=avaliar,
        args=(interacao_id, pergunta, resposta, chunks),
        daemon=True,
        name=f"avaliacao-{interacao_id}",
    )
    thread.start()
