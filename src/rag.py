"""Pipeline RAG. Orquestra roteamento -> recuperacao -> geracao -> telemetria.

Fluxo:
    pergunta
      -> roteador (LLM rapido) classifica categoria e escopo
      -> recuperacao hibrida (vetorial + lexical + RRF)
      -> guarda-corpo: score maximo abaixo do limiar => nao responde
      -> geracao (LLM forte) restrita ao contexto
      -> grava interacao + estatisticas dos chunks
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from . import db, prompts
from .config import config
from .llm import gerar, gerar_json
from .retrieval import ChunkRecuperado, recuperar, tem_contexto_suficiente

log = logging.getLogger(__name__)


@dataclass
class ResultadoRAG:
    interacao_id: int
    resposta: str
    rota: str                       # rag | saudacao | despedida | fora_de_escopo | erro
    categoria: str | None
    chunks: list[ChunkRecuperado]
    sem_contexto: bool
    score_maximo: float | None


def _montar_contexto(chunks: list[ChunkRecuperado]) -> str:
    partes = []
    for i, c in enumerate(chunks, start=1):
        partes.append(f"--- TRECHO {i} (documento: {c.documento}) ---\n{c.conteudo}")
    return "\n\n".join(partes)


TAMANHO_MAX_RESPOSTA_HISTORICO = 500


def _montar_historico(historico: list[dict]) -> str:
    """Formata as trocas anteriores para entrar no prompt. String vazia se
    nao ha historico -- assim a primeira pergunta de uma sessao nao paga
    tokens extra por um bloco vazio."""
    if not historico:
        return ""
    linhas = []
    for turno in historico:
        resposta = turno["resposta"].split("\n\nFontes:")[0].strip()
        if len(resposta) > TAMANHO_MAX_RESPOSTA_HISTORICO:
            # Mantem inicio (assunto) E fim (fechamento/pergunta de
            # continuacao) -- cortar so do inicio pra frente escondia
            # justamente a pergunta de fechamento em respostas longas,
            # fazendo o roteador "esquecer" o que o Henri tinha acabado de
            # oferecer e reescrever um "sim" pra um assunto ja coberto.
            metade = TAMANHO_MAX_RESPOSTA_HISTORICO // 2
            resposta = f"{resposta[:metade]} [...] {resposta[-metade:]}"
        linhas.append(f"Candidato: {turno['pergunta']}\nHenri: {resposta}")
    return "\n\n".join(linhas)


_CARACTERES_MARKDOWN = re.compile(r"[_*`\[]")


def _sanitizar_nome(nome: str | None) -> str | None:
    """O nome vem livre do Telegram e entra em mensagens enviadas com
    parse_mode Markdown (legado, sem escape). Um nome com _, * ou ` sem par
    quebraria o parse da mensagem inteira -- remove esses caracteres."""
    if not nome:
        return None
    limpo = _CARACTERES_MARKDOWN.sub("", nome).strip()
    return limpo or None


def _montar_linha_fontes(chunks: list[ChunkRecuperado]) -> str:
    """Monta 'Fontes: ...' deterministicamente a partir dos chunks usados,
    em vez de confiar no LLM para citar -- ele nao obedece de forma
    consistente e polui a resposta com citacao apos cada frase."""
    nomes = list(dict.fromkeys(c.documento for c in chunks))
    return "Fontes: " + ", ".join(nomes)


def _classificar(pergunta: str, historico_texto: str) -> tuple[str, bool, str]:
    """Roteador barato. Falha aberta: erro no classificador nao bloqueia o RAG.
    Tambem devolve a pergunta reescrita de forma independente do historico --
    necessaria pra recuperacao funcionar quando a pergunta atual sozinha nao
    tem conteudo pesquisavel (ex: "sim" respondendo a uma pergunta do Henri)."""
    try:
        usuario = prompts.ROTEADOR_USUARIO.format(
            historico=historico_texto or "(nenhum)", pergunta=pergunta
        )
        saida = gerar_json(prompts.ROTEADOR_SISTEMA, usuario)
        categoria = saida.get("categoria", "institucional")
        no_escopo = bool(saida.get("no_escopo", True))
        if categoria not in prompts.CATEGORIAS:
            categoria = "institucional"
        pergunta_standalone = (saida.get("pergunta_standalone") or "").strip() or pergunta
        return categoria, no_escopo, pergunta_standalone
    except Exception:
        log.exception("Falha no roteador; seguindo com o RAG mesmo assim.")
        return "institucional", True, pergunta


def responder(pergunta: str, usuario_hash: str, nome: str | None = None) -> ResultadoRAG:
    inicio_total = time.perf_counter()

    nome = _sanitizar_nome(nome)

    historico = db.buscar_historico(
        usuario_hash, config.historico_turnos, config.historico_janela_minutos
    )
    historico_texto = _montar_historico(historico)

    categoria, no_escopo, pergunta_standalone = _classificar(pergunta, historico_texto)

    # --- saudacao ou fora de escopo: nem chega a consultar a base ---
    if not no_escopo:
        if categoria == "saudacao":
            rota = "saudacao"
            # Historico presente: nao repete a apresentacao completa, ja
            # rodada nesta conversa (ex: "ok, tenho outras duvidas").
            if historico_texto:
                resposta = prompts.MENSAGEM_CONTINUACAO
            else:
                abertura = f", {nome}" if nome else ""
                resposta = prompts.MENSAGEM_SAUDACAO.format(abertura=abertura)
        elif categoria == "despedida":
            rota = "despedida"
            fechamento = f", {nome}" if nome else ""
            resposta = prompts.MENSAGEM_DESPEDIDA.format(fechamento=fechamento)
        else:
            rota, resposta = "fora_de_escopo", prompts.MENSAGEM_FORA_ESCOPO

        interacao_id = db.registrar_interacao({
            "usuario_hash": usuario_hash,
            "pergunta": pergunta,
            "categoria_detectada": categoria,
            "rota": rota,
            "resposta": resposta,
            "chunks_recuperados": [],
            "n_chunks": 0,
            "score_medio": None,
            "score_maximo": None,
            "sem_contexto": False,
            "latencia_retrieval_ms": 0,
            "latencia_geracao_ms": 0,
            "latencia_total_ms": int((time.perf_counter() - inicio_total) * 1000),
            "modelo_geracao": None,
            "tokens_entrada": 0,
            "tokens_saida": 0,
        })
        return ResultadoRAG(
            interacao_id=interacao_id,
            resposta=resposta,
            rota=rota,
            categoria=categoria,
            chunks=[],
            sem_contexto=False,
            score_maximo=None,
        )

    # --- recuperacao ---
    # Usa a pergunta reescrita pelo roteador: a pergunta original pode nao
    # ter conteudo pesquisavel sozinha (ex: "sim" respondendo a algo que o
    # proprio Henri perguntou).
    inicio_retrieval = time.perf_counter()
    chunks = recuperar(pergunta_standalone)
    latencia_retrieval = int((time.perf_counter() - inicio_retrieval) * 1000)

    scores = [c.score for c in chunks] or [0.0]
    score_medio = sum(scores) / len(scores)
    score_maximo = max(scores)
    sem_contexto = not tem_contexto_suficiente(chunks)

    # --- guarda-corpo: sem base confiavel, nao gera ---
    if sem_contexto:
        interacao_id = db.registrar_interacao({
            "usuario_hash": usuario_hash,
            "pergunta": pergunta,
            "categoria_detectada": categoria,
            "rota": "rag",
            "resposta": prompts.MENSAGEM_SEM_CONTEXTO,
            "chunks_recuperados": [c.para_log() for c in chunks],
            "n_chunks": len(chunks),
            "score_medio": score_medio,
            "score_maximo": score_maximo,
            "sem_contexto": True,
            "latencia_retrieval_ms": latencia_retrieval,
            "latencia_geracao_ms": 0,
            "latencia_total_ms": int((time.perf_counter() - inicio_total) * 1000),
            "modelo_geracao": None,
            "tokens_entrada": 0,
            "tokens_saida": 0,
        })
        db.registrar_lacuna(interacao_id, pergunta, "sem_contexto")
        return ResultadoRAG(
            interacao_id=interacao_id,
            resposta=prompts.MENSAGEM_SEM_CONTEXTO,
            rota="rag",
            categoria=categoria,
            chunks=chunks,
            sem_contexto=True,
            score_maximo=score_maximo,
        )

    # --- geracao ---
    contexto = _montar_contexto(chunks)
    try:
        # So passa o nome na primeira resposta real da conversa (sem
        # historico ainda) -- evita o LLM repetir o nome em toda resposta,
        # o que ficava artificial ja na segunda troca.
        bloco_nome = f"NOME DO CANDIDATO: {nome}\n\n" if nome and not historico_texto else ""
        bloco_historico = (
            f"HISTORICO DA CONVERSA:\n{historico_texto}\n\n" if historico_texto else ""
        )
        saida = gerar(
            sistema=prompts.GERACAO_SISTEMA,
            usuario=prompts.GERACAO_USUARIO.format(
                nome_bloco=bloco_nome, historico=bloco_historico, contexto=contexto,
                pergunta=pergunta_standalone,
            ),
        )
        resposta_texto = saida.texto.strip() + "\n\n" + _montar_linha_fontes(chunks)
        rota = "rag"
    except Exception:
        log.exception("Falha na geracao.")
        interacao_id = db.registrar_interacao({
            "usuario_hash": usuario_hash,
            "pergunta": pergunta,
            "categoria_detectada": categoria,
            "rota": "erro",
            "resposta": None,
            "chunks_recuperados": [c.para_log() for c in chunks],
            "n_chunks": len(chunks),
            "score_medio": score_medio,
            "score_maximo": score_maximo,
            "sem_contexto": False,
            "latencia_retrieval_ms": latencia_retrieval,
            "latencia_geracao_ms": None,
            "latencia_total_ms": int((time.perf_counter() - inicio_total) * 1000),
            "modelo_geracao": config.modelo_geracao,
            "tokens_entrada": 0,
            "tokens_saida": 0,
        })
        return ResultadoRAG(
            interacao_id=interacao_id,
            resposta="Tive um problema tecnico agora. Pode tentar de novo em instantes?",
            rota="erro",
            categoria=categoria,
            chunks=chunks,
            sem_contexto=False,
            score_maximo=score_maximo,
        )

    interacao_id = db.registrar_interacao({
        "usuario_hash": usuario_hash,
        "pergunta": pergunta,
        "categoria_detectada": categoria,
        "rota": rota,
        "resposta": resposta_texto,
        "chunks_recuperados": [c.para_log() for c in chunks],
        "n_chunks": len(chunks),
        "score_medio": score_medio,
        "score_maximo": score_maximo,
        "sem_contexto": False,
        "latencia_retrieval_ms": latencia_retrieval,
        "latencia_geracao_ms": saida.latencia_ms,
        "latencia_total_ms": int((time.perf_counter() - inicio_total) * 1000),
        "modelo_geracao": saida.modelo,
        "tokens_entrada": saida.tokens_entrada,
        "tokens_saida": saida.tokens_saida,
    })

    db.atualizar_estatisticas_chunks([c.para_log() for c in chunks])

    return ResultadoRAG(
        interacao_id=interacao_id,
        resposta=resposta_texto,
        rota=rota,
        categoria=categoria,
        chunks=chunks,
        sem_contexto=False,
        score_maximo=score_maximo,
    )
