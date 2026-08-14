from src.rag import _montar_historico, _montar_linha_fontes, _sanitizar_nome
from src.retrieval import ChunkRecuperado


def _chunk(documento: str) -> ChunkRecuperado:
    return ChunkRecuperado(1, "conteudo", documento, "cat", None, score=0.9, score_rrf=0.1, origem="ambos")


def test_sanitizar_nome_none():
    assert _sanitizar_nome(None) is None


def test_sanitizar_nome_vazio():
    assert _sanitizar_nome("") is None


def test_sanitizar_nome_sem_caracteres_markdown():
    assert _sanitizar_nome("Joao Silva") == "Joao Silva"


def test_sanitizar_nome_remove_underscore_asterisco_e_crase():
    assert _sanitizar_nome("Jo_ao *Silva* `x`") == "Joao Silva x"


def test_sanitizar_nome_so_caracteres_markdown_vira_none():
    assert _sanitizar_nome("___") is None


def test_montar_historico_vazio():
    assert _montar_historico([]) == ""


def test_montar_historico_uma_troca():
    historico = [{"pergunta": "qual o prazo?", "resposta": "O prazo e 20/08."}]
    texto = _montar_historico(historico)
    assert texto == "Candidato: qual o prazo?\nHenri: O prazo e 20/08."


def test_montar_historico_remove_bloco_de_fontes():
    historico = [{"pergunta": "p", "resposta": "resposta real\n\nFontes: doc1, doc2"}]
    texto = _montar_historico(historico)
    assert "Fontes" not in texto
    assert texto == "Candidato: p\nHenri: resposta real"


def test_montar_historico_trunca_resposta_longa_mantendo_inicio_e_fim():
    resposta = "A" * 300 + "B" * 300
    historico = [{"pergunta": "p", "resposta": resposta}]
    texto = _montar_historico(historico)
    assert " [...] " in texto
    assert texto.startswith("Candidato: p\nHenri: " + "A" * 10)
    assert texto.endswith("B" * 10)
    assert len(texto) < len("Candidato: p\nHenri: " + resposta)


def test_montar_linha_fontes_dedup_mantendo_ordem():
    chunks = [_chunk("doc A"), _chunk("doc B"), _chunk("doc A")]
    assert _montar_linha_fontes(chunks) == "Fontes: doc A, doc B"
