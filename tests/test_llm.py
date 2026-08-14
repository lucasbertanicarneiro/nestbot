from src.llm import _parsear_json_llm, _remover_fence_externo


def test_parsear_json_llm_objeto_valido():
    assert _parsear_json_llm('{"categoria": "beneficios"}') == {"categoria": "beneficios"}


def test_parsear_json_llm_desembrulha_lista_de_um_item():
    assert _parsear_json_llm('[{"categoria": "beneficios"}]') == {"categoria": "beneficios"}


def test_parsear_json_llm_lista_vazia_devolve_dict_vazio():
    assert _parsear_json_llm("[]") == {}


def test_parsear_json_llm_json_invalido_devolve_dict_vazio():
    assert _parsear_json_llm("nao e json") == {}


def test_parsear_json_llm_json_valido_mas_nao_objeto():
    assert _parsear_json_llm("42") == {}
    assert _parsear_json_llm('"texto solto"') == {}


def test_remover_fence_externo_remove_par_unico():
    texto = "```\nconteudo real\n```"
    assert _remover_fence_externo(texto) == "conteudo real"


def test_remover_fence_externo_sem_fence_fica_igual():
    texto = "conteudo sem fence"
    assert _remover_fence_externo(texto) == texto


def test_remover_fence_externo_multiplos_fences_fica_igual():
    # count() != 2 -- nao mexe pra nao arriscar cortar fence de codigo
    # legitimo no meio do conteudo.
    texto = "```python\ncodigo\n```\ntexto\n```\nmais codigo\n```"
    assert _remover_fence_externo(texto) == texto
