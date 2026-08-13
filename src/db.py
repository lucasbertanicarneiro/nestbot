"""Camada de acesso ao Postgres. Pool unico compartilhado pela aplicacao."""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

from .config import config

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def _configurar_conexao(conn: psycopg.Connection) -> None:
    """Registra o adaptador do pgvector em toda conexao nova do pool."""
    register_vector(conn)


def obter_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=config.dsn,
            min_size=1,
            max_size=8,
            timeout=config.db_pool_timeout_seg,
            configure=_configurar_conexao,
            open=True,
        )
        log.info("Pool de conexoes iniciado.")
    return _pool


@contextmanager
def cursor(dict_rows: bool = True) -> Iterator[psycopg.Cursor]:
    """Cursor transacional. Commit no sucesso, rollback na excecao."""
    pool = obter_pool()
    with pool.connection() as conn:
        row_factory = dict_row if dict_rows else None
        with conn.cursor(row_factory=row_factory) as cur:
            yield cur


def fechar_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ------------------------------------------------------------------
# Privacidade
# ------------------------------------------------------------------

def hashear_usuario(telegram_id: int | str) -> str:
    """Nunca gravamos o id do Telegram em claro. Hash com sal."""
    bruto = f"{config.sal_hash}:{telegram_id}".encode("utf-8")
    return hashlib.sha256(bruto).hexdigest()[:32]


# ------------------------------------------------------------------
# Escritas de telemetria
# ------------------------------------------------------------------

def registrar_interacao(dados: dict[str, Any]) -> int:
    """Grava uma interacao e devolve o id gerado."""
    sql = """
        INSERT INTO interacoes (
            usuario_hash, pergunta, categoria_detectada, rota, resposta,
            chunks_recuperados, n_chunks, score_medio, score_maximo, sem_contexto,
            latencia_retrieval_ms, latencia_geracao_ms, latencia_total_ms,
            modelo_geracao, tokens_entrada, tokens_saida
        ) VALUES (
            %(usuario_hash)s, %(pergunta)s, %(categoria_detectada)s, %(rota)s, %(resposta)s,
            %(chunks_recuperados)s, %(n_chunks)s, %(score_medio)s, %(score_maximo)s, %(sem_contexto)s,
            %(latencia_retrieval_ms)s, %(latencia_geracao_ms)s, %(latencia_total_ms)s,
            %(modelo_geracao)s, %(tokens_entrada)s, %(tokens_saida)s
        )
        RETURNING id;
    """
    payload = dict(dados)
    payload["chunks_recuperados"] = json.dumps(
        payload.get("chunks_recuperados", []), ensure_ascii=False
    )
    with cursor() as cur:
        cur.execute(sql, payload)
        return cur.fetchone()["id"]


def registrar_avaliacao(interacao_id: int, avaliacao: dict[str, Any]) -> None:
    sql = """
        INSERT INTO avaliacoes (
            interacao_id, faithfulness, relevancia_resposta,
            relevancia_contexto, justificativa, modelo_avaliador
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (interacao_id) DO UPDATE SET
            faithfulness        = EXCLUDED.faithfulness,
            relevancia_resposta = EXCLUDED.relevancia_resposta,
            relevancia_contexto = EXCLUDED.relevancia_contexto,
            justificativa       = EXCLUDED.justificativa,
            modelo_avaliador    = EXCLUDED.modelo_avaliador;
    """
    with cursor() as cur:
        cur.execute(
            sql,
            (
                interacao_id,
                avaliacao.get("faithfulness"),
                avaliacao.get("relevancia_resposta"),
                avaliacao.get("relevancia_contexto"),
                avaliacao.get("justificativa"),
                avaliacao.get("modelo_avaliador"),
            ),
        )


def registrar_feedback(interacao_id: int, util: bool) -> None:
    sql = """
        INSERT INTO feedback (interacao_id, util) VALUES (%s, %s)
        ON CONFLICT (interacao_id) DO UPDATE SET util = EXCLUDED.util;
    """
    with cursor() as cur:
        cur.execute(sql, (interacao_id, util))


def registrar_lacuna(interacao_id: int | None, pergunta: str, motivo: str) -> None:
    """Toda pergunta mal respondida vira item de backlog da base."""
    sql = """
        INSERT INTO lacunas_conhecimento (interacao_id, pergunta, motivo)
        VALUES (%s, %s, %s);
    """
    with cursor() as cur:
        cur.execute(sql, (interacao_id, pergunta, motivo))


def buscar_historico(usuario_hash: str, limite: int, janela_minutos: int) -> list[dict[str, Any]]:
    """Ultimas trocas 'de verdade' do usuario: rota=rag, com resposta, dentro
    da janela de tempo. Ignora saudacao/fora_de_escopo (nao agregam contexto
    de conversa) e respostas sem_contexto (nao ha fato novo pra lembrar). A
    janela de tempo evita reviver uma conversa de dias atras como se fosse a
    mesma sessao."""
    sql = """
        SELECT pergunta, resposta, criado_em
          FROM interacoes
         WHERE usuario_hash = %s
           AND rota = 'rag'
           AND sem_contexto = FALSE
           AND resposta IS NOT NULL
           AND criado_em > now() - (%s || ' minutes')::interval
         ORDER BY criado_em DESC
         LIMIT %s;
    """
    with cursor() as cur:
        cur.execute(sql, (usuario_hash, janela_minutos, limite))
        return list(reversed(cur.fetchall()))


def atualizar_estatisticas_chunks(chunks: list[dict[str, Any]]) -> None:
    """Incrementa contadores usados pela view de cobertura da base."""
    if not chunks:
        return
    sql = """
        UPDATE chunks
           SET vezes_recuperado = vezes_recuperado + 1,
               soma_scores      = soma_scores + %s
         WHERE id = %s;
    """
    with cursor() as cur:
        for c in chunks:
            cur.execute(sql, (float(c["score"]), int(c["chunk_id"])))
