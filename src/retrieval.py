"""Recuperacao hibrida: vetorial + lexical, fundidos por RRF.

Por que hibrido: busca puramente semantica erra termo exato e entidade
rara -- "AWS Cloud Practitioner", "9 de setembro", "Ituiutaba". A busca
lexical do Postgres (tsvector portugues) cobre justamente esse buraco.

RRF (Reciprocal Rank Fusion) funde as duas listas usando a POSICAO, nao
o score. Isso evita ter que normalizar escalas incompativeis (distancia
de cosseno vs ts_rank).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .config import config
from .db import cursor
from .embeddings import embedar_consulta

log = logging.getLogger(__name__)

# Palavras muito frequentes em portugues que sujam a busca lexical.
_STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na",
    "nos", "nas", "um", "uma", "para", "por", "com", "que", "qual", "quais",
    "como", "quanto", "quando", "onde", "se", "eu", "meu", "minha", "ser", "e",
}


@dataclass
class ChunkRecuperado:
    chunk_id: int
    conteudo: str
    documento: str
    categoria: str
    url: str | None
    score: float          # similaridade de cosseno (0..1), usada como confianca
    score_rrf: float      # score de fusao, usado so para ordenar
    origem: str           # vetorial | lexical | ambos

    def para_log(self) -> dict:
        """Versao enxuta que vai para o JSONB de auditoria."""
        return {
            "chunk_id": self.chunk_id,
            "documento": self.documento,
            "categoria": self.categoria,
            "score": round(self.score, 4),
            "origem": self.origem,
            "previa": self.conteudo[:120],
        }


def _preparar_consulta_lexical(pergunta: str) -> str:
    """Monta a expressao para websearch_to_tsquery, sem stopwords."""
    termos = re.findall(r"\w+", pergunta.lower(), flags=re.UNICODE)
    uteis = [t for t in termos if t not in _STOPWORDS and len(t) > 2]
    return " or ".join(uteis) if uteis else pergunta


def _buscar_vetorial(pergunta: str, limite: int) -> list[dict]:
    vetor = embedar_consulta(pergunta)
    sql = """
        SELECT c.id, c.conteudo, d.titulo AS documento, d.categoria, d.url,
               1 - (c.embedding <=> %s::vector) AS similaridade
          FROM chunks c
          JOIN documentos d ON d.id = c.documento_id
      ORDER BY c.embedding <=> %s::vector
         LIMIT %s;
    """
    with cursor() as cur:
        cur.execute(sql, (vetor, vetor, limite))
        return cur.fetchall()


def _buscar_lexical(pergunta: str, limite: int) -> list[dict]:
    consulta = _preparar_consulta_lexical(pergunta)
    sql = """
        SELECT c.id, c.conteudo, d.titulo AS documento, d.categoria, d.url,
               ts_rank(c.tsv, websearch_to_tsquery('portuguese', %s)) AS rank_lexical
          FROM chunks c
          JOIN documentos d ON d.id = c.documento_id
         WHERE c.tsv @@ websearch_to_tsquery('portuguese', %s)
      ORDER BY rank_lexical DESC
         LIMIT %s;
    """
    with cursor() as cur:
        cur.execute(sql, (consulta, consulta, limite))
        return cur.fetchall()


def recuperar(pergunta: str) -> list[ChunkRecuperado]:
    """Executa as duas buscas, funde por RRF e devolve o top_k_final."""
    vetoriais = _buscar_vetorial(pergunta, config.top_k_vetorial)
    lexicais = _buscar_lexical(pergunta, config.top_k_lexical)

    k = config.rrf_k
    pontos: dict[int, float] = {}
    origens: dict[int, set[str]] = {}
    dados: dict[int, dict] = {}
    similaridades: dict[int, float] = {}

    for posicao, linha in enumerate(vetoriais, start=1):
        cid = linha["id"]
        pontos[cid] = pontos.get(cid, 0.0) + 1.0 / (k + posicao)
        origens.setdefault(cid, set()).add("vetorial")
        dados[cid] = linha
        similaridades[cid] = float(linha["similaridade"])

    for posicao, linha in enumerate(lexicais, start=1):
        cid = linha["id"]
        pontos[cid] = pontos.get(cid, 0.0) + 1.0 / (k + posicao)
        origens.setdefault(cid, set()).add("lexical")
        dados.setdefault(cid, linha)
        # chunk que so apareceu na busca lexical nao tem similaridade calculada
        similaridades.setdefault(cid, 0.0)

    ordenados = sorted(pontos.items(), key=lambda item: item[1], reverse=True)

    resultado: list[ChunkRecuperado] = []
    for cid, score_rrf in ordenados[: config.top_k_final]:
        linha = dados[cid]
        origem_set = origens[cid]
        resultado.append(
            ChunkRecuperado(
                chunk_id=cid,
                conteudo=linha["conteudo"],
                documento=linha["documento"],
                categoria=linha["categoria"],
                url=linha.get("url"),
                score=similaridades[cid],
                score_rrf=score_rrf,
                origem="ambos" if len(origem_set) > 1 else next(iter(origem_set)),
            )
        )

    log.debug(
        "Recuperacao: %d vetoriais, %d lexicais, %d finais",
        len(vetoriais), len(lexicais), len(resultado),
    )
    return resultado


def tem_contexto_suficiente(chunks: list[ChunkRecuperado]) -> bool:
    """Guarda-corpo contra alucinacao: sem chunk confiavel, nao respondemos."""
    if not chunks:
        return False
    return max(c.score for c in chunks) >= config.limiar_similaridade
