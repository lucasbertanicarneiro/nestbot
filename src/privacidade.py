"""Redacao de PII na pergunta do usuario, antes de ela ir pra qualquer lugar.

Por que aqui e nao so na hora de gravar: a pergunta tambem viaja pro Groq
(roteador, gerador e avaliador sao 3 chamadas por interacao) -- redigir so
no INSERT protegeria o banco mas nao o terceiro. Redigir na entrada, em
bot.py, protege as duas pontas de uma vez.

E best-effort por regex, nao um scrubber de compliance: cobre os formatos
mais comuns de CPF, telefone, e-mail e CEP em portugues, mas nao pega tudo
(ex: CPF sem pontuacao pode ser rotulado como telefone -- ambiguidade
inerente a um numero puro de 11 digitos). Prioriza nao vazar sobre acertar
o rotulo.
"""
from __future__ import annotations

import re

_PADROES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[e-mail removido]"),
    (re.compile(r"\b\d{3}[.\s]\d{3}[.\s]\d{3}[-\s]\d{2}\b"), "[CPF removido]"),
    (re.compile(r"\b\d{5}-\d{3}\b"), "[CEP removido]"),
    (
        re.compile(r"(?:\+?55\s?)?\(?\d{2}\)?[\s.-]?9?\d{4}-?\d{4}\b"),
        "[telefone removido]",
    ),
]


def redigir_pii(texto: str) -> tuple[str, bool]:
    """Substitui trechos que parecem PII por marcadores. Devolve o texto
    (redigido ou nao) e se algo foi removido."""
    houve_redacao = False
    for padrao, marcador in _PADROES:
        texto, n = padrao.subn(marcador, texto)
        if n:
            houve_redacao = True
    return texto, houve_redacao
