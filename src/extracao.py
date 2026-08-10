"""Extracao de PDF: texto (deterministico, sem LLM) e paginas como imagem.

Transcricao de imagem NAO mora aqui -- e uma chamada de modelo, entao vive em
llm.transcrever_imagem, junto com o resto do acesso ao Groq. Este modulo so
extrai texto ou renderiza paginas; nunca importa config nem fala com rede.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader

# Abaixo disso (media de caracteres por pagina) suspeitamos de PDF escaneado:
# o texto "extraido" e ruido de fonte incorporada, nao conteudo de verdade.
LIMIAR_CHARS_POR_PAGINA = 50

# Abaixo disso (media de palavras por linha nao-vazia) suspeitamos de PDF
# "picotado": infografico/slide onde cada caixa vira uma linha solta e a
# extracao de texto perde o pareamento espacial entre rotulo e valor -- foi
# exatamente esse padrao que causou uma resposta errada do bot (etapa "Fator
# H" marcada como eliminatoria quando o documento dizia o contrario).
LIMIAR_PALAVRAS_POR_LINHA = 3.0


@dataclass
class ExtracaoPdf:
    texto: str
    n_paginas: int
    aviso: str | None
    # Sinal estrutural pro import_web.py decidir se cai no fallback de
    # extracao por imagem -- nao e so uma mensagem informativa.
    precisa_fallback_visual: bool


def extrair_texto_pdf(dados: bytes) -> ExtracaoPdf:
    leitor = PdfReader(io.BytesIO(dados))
    paginas = [pagina.extract_text() or "" for pagina in leitor.pages]
    texto = "\n\n".join(p.strip() for p in paginas if p.strip())

    n_paginas = len(paginas)
    media_chars = len(texto) / n_paginas if n_paginas else 0

    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    media_palavras = sum(len(l.split()) for l in linhas) / len(linhas) if linhas else 0

    aviso = None
    precisa_fallback_visual = False

    if n_paginas == 0:
        aviso = "PDF sem paginas."
        precisa_fallback_visual = True
    elif media_chars < LIMIAR_CHARS_POR_PAGINA:
        aviso = (
            "Quase nenhum texto foi extraido -- este PDF provavelmente e "
            "escaneado (imagem, nao texto). Extraindo por imagem em vez de "
            "texto puro."
        )
        precisa_fallback_visual = True
    elif media_palavras < LIMIAR_PALAVRAS_POR_LINHA:
        aviso = (
            "O texto extraido tem linhas muito curtas/soltas -- este PDF "
            "provavelmente e visual (infografico, slide) e a extracao de "
            "texto perde o pareamento entre rotulo e valor. Extraindo por "
            "imagem em vez de texto puro."
        )
        precisa_fallback_visual = True

    return ExtracaoPdf(
        texto=texto,
        n_paginas=n_paginas,
        aviso=aviso,
        precisa_fallback_visual=precisa_fallback_visual,
    )


def pdf_para_imagens(dados: bytes, dpi: int = 150) -> list[bytes]:
    """Renderiza cada pagina do PDF como PNG (pdf2image/poppler).

    Usado quando extrair_texto_pdf sinaliza precisa_fallback_visual: a
    pagina como imagem preserva o pareamento espacial que o texto puro
    perde, e pode ser transcrita por llm.transcrever_imagem.
    """
    from pdf2image import convert_from_bytes

    paginas = convert_from_bytes(dados, dpi=dpi)
    saida = []
    for pagina in paginas:
        buffer = io.BytesIO()
        pagina.save(buffer, format="PNG")
        saida.append(buffer.getvalue())
    return saida
