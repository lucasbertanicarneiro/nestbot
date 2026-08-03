"""Cliente Groq. Trata rate limit (429) com backoff exponencial."""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from groq import Groq, RateLimitError, APIStatusError

from .config import config

log = logging.getLogger(__name__)

_cliente = Groq(api_key=config.groq_api_key)

MAX_TENTATIVAS = 4


@dataclass
class RespostaLLM:
    texto: str
    modelo: str
    tokens_entrada: int
    tokens_saida: int
    latencia_ms: int


def _chamar(
    mensagens: list[dict[str, str]],
    modelo: str,
    temperatura: float,
    max_tokens: int,
    json_mode: bool = False,
) -> RespostaLLM:
    inicio = time.perf_counter()
    kwargs: dict[str, Any] = {
        "model": modelo,
        "messages": mensagens,
        "temperature": temperatura,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    ultima_excecao: Exception | None = None
    for tentativa in range(MAX_TENTATIVAS):
        try:
            resp = _cliente.chat.completions.create(**kwargs)
            return RespostaLLM(
                texto=resp.choices[0].message.content or "",
                modelo=modelo,
                tokens_entrada=resp.usage.prompt_tokens if resp.usage else 0,
                tokens_saida=resp.usage.completion_tokens if resp.usage else 0,
                latencia_ms=int((time.perf_counter() - inicio) * 1000),
            )
        except RateLimitError as e:
            ultima_excecao = e
            # Free tier do Groq: 30 RPM. Backoff com jitter evita rajada.
            espera = (2**tentativa) + random.uniform(0, 1)
            log.warning("Rate limit do Groq. Tentativa %d, aguardando %.1fs", tentativa + 1, espera)
            time.sleep(espera)
        except APIStatusError as e:
            ultima_excecao = e
            if e.status_code >= 500:
                time.sleep(2**tentativa)
                continue
            raise

    raise RuntimeError(f"Groq indisponivel apos {MAX_TENTATIVAS} tentativas") from ultima_excecao


def gerar(
    sistema: str,
    usuario: str,
    modelo: str | None = None,
    temperatura: float = 0.2,
    max_tokens: int = 900,
) -> RespostaLLM:
    """Geracao de texto livre (resposta final ao candidato)."""
    return _chamar(
        mensagens=[
            {"role": "system", "content": sistema},
            {"role": "user", "content": usuario},
        ],
        modelo=modelo or config.modelo_geracao,
        temperatura=temperatura,
        max_tokens=max_tokens,
    )


def gerar_json(
    sistema: str,
    usuario: str,
    modelo: str | None = None,
    temperatura: float = 0.0,
    max_tokens: int = 500,
) -> dict[str, Any]:
    """Geracao estruturada. Usada pelo roteador e pelo avaliador."""
    resposta = _chamar(
        mensagens=[
            {"role": "system", "content": sistema},
            {"role": "user", "content": usuario},
        ],
        modelo=modelo or config.modelo_rapido,
        temperatura=temperatura,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        return json.loads(resposta.texto)
    except json.JSONDecodeError:
        log.error("LLM devolveu JSON invalido: %s", resposta.texto[:300])
        return {}
