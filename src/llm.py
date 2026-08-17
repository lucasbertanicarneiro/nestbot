"""Cliente Groq. Trata rate limit (429) com backoff exponencial."""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from groq import Groq, RateLimitError, APIStatusError

from . import prompts
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
    mensagens: list[dict[str, Any]],
    modelo: str,
    temperatura: float,
    max_tokens: int,
    json_mode: bool = False,
    reasoning_format: str | None = None,
    reasoning_effort: str | None = None,
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
    if reasoning_format or reasoning_effort:
        # Parametros novos, ainda nao tipados no SDK instalado (groq==0.13.1) --
        # extra_body repassa direto pro corpo da requisicao HTTP.
        extra_body: dict[str, Any] = {}
        if reasoning_format:
            extra_body["reasoning_format"] = reasoning_format
        if reasoning_effort:
            extra_body["reasoning_effort"] = reasoning_effort
        kwargs["extra_body"] = extra_body

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
    temperatura: float = 0.5,
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
        # modelo_rapido (gpt-oss) e "thinking". reasoning_format="hidden" so
        # tira o raciocinio da resposta, mas ele ainda consome max_tokens
        # internamente -- em tarefa de classificacao simples isso as vezes
        # estourava o budget inteiro e nao sobrava nada pro JSON de saida
        # (resposta vazia, falha a validacao do response_format).
        # reasoning_effort="low" mantem o raciocinio curto o bastante pra
        # sempre sobrar espaco.
        reasoning_format="hidden",
        reasoning_effort="low",
    )
    return _parsear_json_llm(resposta.texto)


def _parsear_json_llm(texto: str) -> dict[str, Any]:
    """Interpreta o texto devolvido pela API como um objeto JSON, tolerando
    os dois jeitos de a Groq fugir do formato esperado."""
    try:
        dado = json.loads(texto)
    except json.JSONDecodeError:
        log.error("LLM devolveu JSON invalido: %s", texto[:300])
        return {}

    # Groq as vezes embrulha o objeto numa lista de 1 item.
    if isinstance(dado, list):
        dado = dado[0] if dado and isinstance(dado[0], dict) else {}

    if not isinstance(dado, dict):
        log.error("LLM devolveu JSON que nao e um objeto: %s", texto[:300])
        return {}

    return dado


def _remover_fence_externo(texto: str) -> str:
    """Alguns modelos embrulham a resposta inteira num bloco de codigo
    mesmo quando instruidos a nao fazer isso -- remove so o fence externo,
    se for exatamente um par abrindo/fechando em volta de tudo."""
    texto = texto.strip()
    if texto.startswith("```") and texto.endswith("```") and texto.count("```") == 2:
        linhas = texto.split("\n")
        return "\n".join(linhas[1:-1]).strip()
    return texto


def transcrever_imagem(imagem_b64: str, mime: str, modelo: str | None = None) -> RespostaLLM:
    """Transcreve print/imagem em markdown. Usada pelo importador (src/import_web.py)."""
    mensagens: list[dict[str, Any]] = [
        {"role": "system", "content": prompts.IMPORTADOR_VISAO_SISTEMA},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Transcreva o conteudo desta imagem em markdown."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{imagem_b64}"}},
            ],
        },
    ]
    resposta = _chamar(
        mensagens=mensagens,
        modelo=modelo or config.modelo_visao,
        temperatura=0.1,
        max_tokens=2500,
        # qwen3.6 e um modelo "thinking" -- sem isso, o <think>...</think>
        # vaza pro corpo do documento que vai pra base.
        reasoning_format="hidden",
    )
    resposta.texto = _remover_fence_externo(resposta.texto)
    return resposta
