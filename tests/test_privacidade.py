from src.privacidade import redigir_pii


def test_redige_cpf_formatado():
    texto, houve = redigir_pii("meu cpf e 123.456.789-00, pode confirmar?")
    assert "[CPF removido]" in texto
    assert "123.456.789-00" not in texto
    assert houve is True


def test_redige_cpf_separado_por_espaco():
    texto, houve = redigir_pii("meu cpf e 776 889 543 00")
    assert "[CPF removido]" in texto
    assert "776 889 543 00" not in texto
    assert houve is True


def test_redige_email():
    texto, houve = redigir_pii("me manda a resposta em joao.silva@gmail.com por favor")
    assert "[e-mail removido]" in texto
    assert "joao.silva@gmail.com" not in texto
    assert houve is True


def test_redige_telefone_com_ddd_e_parenteses():
    texto, houve = redigir_pii("pode me ligar no (11) 91234-5678?")
    assert "[telefone removido]" in texto
    assert "91234-5678" not in texto
    assert houve is True


def test_redige_telefone_sem_formatacao():
    texto, houve = redigir_pii("meu numero e 11912345678")
    assert "[telefone removido]" in texto
    assert "11912345678" not in texto
    assert houve is True


def test_redige_cep():
    texto, houve = redigir_pii("moro no cep 38300-000")
    assert "[CEP removido]" in texto
    assert "38300-000" not in texto
    assert houve is True


def test_redige_varios_no_mesmo_texto():
    texto, houve = redigir_pii(
        "sou joao@teste.com, cpf 123.456.789-00, tel (11) 91234-5678"
    )
    assert houve is True
    assert "[e-mail removido]" in texto
    assert "[CPF removido]" in texto
    assert "[telefone removido]" in texto


def test_texto_sem_pii_fica_intacto():
    original = "quais sao os requisitos pra me inscrever no programa de trainee?"
    texto, houve = redigir_pii(original)
    assert texto == original
    assert houve is False


def test_datas_e_numeros_pequenos_nao_disparam_falso_positivo():
    original = (
        "as inscricoes vao de 27 de julho a 9 de setembro de 2026, e as "
        "rotacoes duram entre 4 e 12 meses"
    )
    texto, houve = redigir_pii(original)
    assert texto == original
    assert houve is False
