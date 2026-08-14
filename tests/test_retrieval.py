from src.retrieval import _fundir_rrf, _preparar_consulta_lexical, tem_contexto_suficiente
from src.retrieval import ChunkRecuperado


def _linha(cid: int, similaridade: float | None = None) -> dict:
    linha = {
        "id": cid,
        "conteudo": f"conteudo {cid}",
        "documento": f"doc {cid}",
        "categoria": "institucional",
        "url": None,
    }
    if similaridade is not None:
        linha["similaridade"] = similaridade
    return linha


def test_fundir_rrf_marca_origem_ambos_quando_aparece_nas_duas_buscas():
    vetoriais = [_linha(1, 0.9)]
    lexicais = [_linha(1)]

    resultado = _fundir_rrf(vetoriais, lexicais, k=60, top_k=4)

    assert len(resultado) == 1
    assert resultado[0].origem == "ambos"
    assert resultado[0].score == 0.9


def test_fundir_rrf_chunk_so_vetorial_usa_sua_similaridade():
    vetoriais = [_linha(1, 0.75)]
    lexicais: list[dict] = []

    resultado = _fundir_rrf(vetoriais, lexicais, k=60, top_k=4)

    assert resultado[0].origem == "vetorial"
    assert resultado[0].score == 0.75


def test_fundir_rrf_chunk_so_lexical_tem_score_zero():
    vetoriais: list[dict] = []
    lexicais = [_linha(1)]

    resultado = _fundir_rrf(vetoriais, lexicais, k=60, top_k=4)

    assert resultado[0].origem == "lexical"
    assert resultado[0].score == 0.0


def test_fundir_rrf_respeita_top_k_e_ordena_por_score_rrf():
    # chunk 1 aparece nas duas buscas em 1o lugar -- deve ficar na frente
    # de um chunk que so aparece numa delas.
    vetoriais = [_linha(1, 0.9), _linha(2, 0.8)]
    lexicais = [_linha(1), _linha(3)]

    resultado = _fundir_rrf(vetoriais, lexicais, k=60, top_k=2)

    assert len(resultado) == 2
    assert resultado[0].chunk_id == 1
    assert resultado[0].score_rrf >= resultado[1].score_rrf


def test_preparar_consulta_lexical_remove_stopwords_e_palavras_curtas():
    consulta = _preparar_consulta_lexical("Qual o prazo para se inscrever?")
    assert "qual" not in consulta
    assert "para" not in consulta
    assert "prazo" in consulta
    assert "inscrever" in consulta


def test_preparar_consulta_lexical_sem_termo_util_usa_pergunta_original():
    consulta = _preparar_consulta_lexical("e o")
    assert consulta == "e o"


def test_tem_contexto_suficiente_sem_chunks_e_falso():
    assert tem_contexto_suficiente([]) is False


def test_tem_contexto_suficiente_acima_do_limiar():
    chunks = [
        ChunkRecuperado(1, "c", "d", "cat", None, score=0.9, score_rrf=0.1, origem="vetorial"),
    ]
    assert tem_contexto_suficiente(chunks) is True


def test_tem_contexto_suficiente_abaixo_do_limiar():
    chunks = [
        ChunkRecuperado(1, "c", "d", "cat", None, score=0.1, score_rrf=0.1, origem="vetorial"),
    ]
    assert tem_contexto_suficiente(chunks) is False
