"""Ingestao da base de conhecimento.

Estrategia de chunking: quebra por cabecalho markdown primeiro
(fronteira semantica natural) e so depois por tamanho. Isso preserva
o contexto de secao e evita cortar uma tabela de beneficios no meio.

Uso:
    python -m src.ingest                # ingere data/knowledge/
    python -m src.ingest --recriar      # limpa a base antes
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
from pathlib import Path

import frontmatter

from .config import config
from .db import cursor
from .embeddings import embedar_passagens

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DIR_CONHECIMENTO = Path(__file__).resolve().parent.parent / "data" / "knowledge"


# ------------------------------------------------------------------
# Chunking
# ------------------------------------------------------------------

def _quebrar_por_secao(texto: str) -> list[tuple[str, str]]:
    """Divide o markdown em (titulo_secao, conteudo)."""
    linhas = texto.split("\n")
    secoes: list[tuple[str, list[str]]] = []
    titulo_atual = "Introducao"
    buffer: list[str] = []

    for linha in linhas:
        if re.match(r"^#{1,6}\s+", linha):
            if buffer:
                secoes.append((titulo_atual, buffer))
            titulo_atual = re.sub(r"^#{1,6}\s+", "", linha).strip()
            buffer = []
        else:
            buffer.append(linha)

    if buffer:
        secoes.append((titulo_atual, buffer))

    return [(t, "\n".join(c).strip()) for t, c in secoes if "\n".join(c).strip()]


def _quebrar_por_tamanho(texto: str, tamanho: int, sobreposicao: int) -> list[str]:
    """Quebra respeitando fim de paragrafo/frase quando possivel."""
    if len(texto) <= tamanho:
        return [texto]

    pedacos: list[str] = []
    inicio = 0
    while inicio < len(texto):
        fim = min(inicio + tamanho, len(texto))
        if fim < len(texto):
            # procura uma fronteira decente nos ultimos 200 chars
            janela = texto[max(inicio, fim - 200):fim]
            for separador in ("\n\n", ". ", "\n", "; "):
                pos = janela.rfind(separador)
                if pos != -1:
                    fim = max(inicio, fim - 200) + pos + len(separador)
                    break
        pedaco = texto[inicio:fim].strip()
        if pedaco:
            pedacos.append(pedaco)
        if fim >= len(texto):
            break
        inicio = max(fim - sobreposicao, inicio + 1)
    return pedacos


def montar_chunks(titulo_doc: str, texto: str) -> list[str]:
    """Cada chunk carrega o breadcrumb 'documento > secao' como cabecalho.

    Isso e 'contextual chunking': o chunk isolado continua fazendo
    sentido quando cai no prompt sem os vizinhos.
    """
    chunks: list[str] = []
    for titulo_secao, conteudo in _quebrar_por_secao(texto):
        for pedaco in _quebrar_por_tamanho(
            conteudo, config.chunk_tamanho, config.chunk_sobreposicao
        ):
            chunks.append(f"[{titulo_doc} > {titulo_secao}]\n{pedaco}")
    return chunks


# ------------------------------------------------------------------
# Persistencia
# ------------------------------------------------------------------

def _hash(conteudo: str) -> str:
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def ingerir_arquivo(caminho: Path) -> int:
    """Ingere um .md com frontmatter. Devolve quantos chunks gravou."""
    doc = frontmatter.load(caminho)
    meta = doc.metadata
    conteudo = doc.content.strip()

    titulo = meta.get("titulo", caminho.stem)
    fonte = meta.get("fonte", "desconhecida")
    url = meta.get("url")
    categoria = meta.get("categoria", "institucional")
    hash_conteudo = _hash(conteudo)

    with cursor() as cur:
        cur.execute("SELECT id FROM documentos WHERE hash_conteudo = %s", (hash_conteudo,))
        if cur.fetchone():
            log.info("  inalterado, pulando: %s", caminho.name)
            return 0

        # conteudo mudou -> remove versao antiga do mesmo titulo+fonte
        cur.execute("DELETE FROM documentos WHERE titulo = %s AND fonte = %s", (titulo, fonte))
        cur.execute(
            """INSERT INTO documentos (titulo, fonte, url, categoria, hash_conteudo)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (titulo, fonte, url, categoria, hash_conteudo),
        )
        documento_id = cur.fetchone()["id"]

    chunks = montar_chunks(titulo, conteudo)
    if not chunks:
        log.warning("  nenhum chunk gerado: %s", caminho.name)
        return 0

    vetores = embedar_passagens(chunks)

    with cursor() as cur:
        for ordem, (texto_chunk, vetor) in enumerate(zip(chunks, vetores)):
            cur.execute(
                """INSERT INTO chunks
                       (documento_id, ordem, conteudo, n_caracteres, embedding, modelo_embedding)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    documento_id,
                    ordem,
                    texto_chunk,
                    len(texto_chunk),
                    vetor,
                    config.modelo_embedding,
                ),
            )

    log.info("  %s -> %d chunks", caminho.name, len(chunks))
    return len(chunks)


def ingerir_tudo(recriar: bool = False) -> None:
    if recriar:
        log.warning("Limpando base de conhecimento...")
        with cursor() as cur:
            cur.execute("TRUNCATE documentos RESTART IDENTITY CASCADE")

    arquivos = sorted(DIR_CONHECIMENTO.glob("*.md"))
    if not arquivos:
        log.error("Nenhum .md em %s", DIR_CONHECIMENTO)
        return

    log.info("Ingerindo %d arquivo(s)...", len(arquivos))
    total = sum(ingerir_arquivo(a) for a in arquivos)

    with cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM chunks")
        n_chunks = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM documentos")
        n_docs = cur.fetchone()["n"]

    log.info("Concluido. +%d chunks nesta rodada.", total)
    log.info("Base atual: %d documentos, %d chunks.", n_docs, n_chunks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingestao da base de conhecimento do Henri")
    parser.add_argument("--recriar", action="store_true", help="limpa a base antes de ingerir")
    args = parser.parse_args()
    ingerir_tudo(recriar=args.recriar)
