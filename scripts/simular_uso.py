"""Popula o banco com trafego real para o dashboard ter o que mostrar.

Problema pratico: um Power BI com 3 linhas nao demonstra nada. Este script
roda um lote de perguntas de verdade pelo pipeline completo -- recuperacao,
geracao e avaliacao acontecem de fato, ninguem esta inventando numero.

    python scripts/simular_uso.py --n 40

Cuidado com o free tier do Groq: 30 requisicoes por minuto. Cada pergunta
consome ~3 chamadas (roteador, geracao, avaliador), entao o script espaca
as execucoes por padrao.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, evaluation, rag  # noqa: E402

# Perguntas reais que candidatos fazem. Inclui de proposito algumas que a
# base NAO cobre -- sao elas que alimentam o painel de lacunas.
PERGUNTAS = [
    "qual o prazo final de inscricao?",
    "quando fecha a inscricao?",
    "posso me inscrever sendo tecnologo?",
    "aceita qualquer curso de graduacao?",
    "preciso saber ingles?",
    "tem limite de idade?",
    "quais sao as etapas do processo seletivo?",
    "as entrevistas sao online ou presenciais?",
    "quem paga a passagem se eu for pra etapa final em SP?",
    "como recebo o resultado?",
    "vale a pena terminar a trilha rapido?",
    "o processo e por ordem de chegada?",
    "quais beneficios o programa oferece?",
    "tem plano de saude?",
    "o programa paga pos-graduacao?",
    "o que e o Trainee Hub?",
    "tem participacao nos lucros?",
    "qual o salario do trainee?",
    "quanto ganha um trainee da Nestle?",
    "vou precisar mudar de cidade?",
    "em quais estados a Nestle tem fabrica?",
    "tem vaga em Sao Paulo?",
    "quanto tempo dura cada rotacao?",
    "quanto tempo dura o programa?",
    "sou efetivado no final?",
    "qual a taxa de efetivacao?",
    "quais areas tem vaga?",
    "tem vaga na area de tecnologia?",
    "quando comeca o programa?",
    "o que a Nestle valoriza num trainee?",
    # fora da cobertura da base -> viram lacunas
    "qual a nota de corte do teste de logica?",
    "quantas pessoas se inscreveram esse ano?",
    "posso trabalhar remoto?",
    "tem vaga em Jundiai?",
    "qual o percentual de aprovados por area?",
    "tem cota para PCD?",
    # fora de escopo -> testam o roteador
    "me ensina a fazer bolo de cenoura",
    "qual a capital da Franca?",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulador de trafego do Henri")
    parser.add_argument("--n", type=int, default=40, help="quantidade de perguntas")
    parser.add_argument("--pausa", type=float, default=2.5, help="segundos entre perguntas")
    parser.add_argument("--sem-avaliacao", action="store_true")
    parser.add_argument("--usuarios", type=int, default=8, help="usuarios distintos simulados")
    args = parser.parse_args()

    usuarios = [db.hashear_usuario(f"simulado-{i}") for i in range(args.usuarios)]

    print(f"Simulando {args.n} interacoes...\n")
    for i in range(args.n):
        pergunta = random.choice(PERGUNTAS)
        usuario = random.choice(usuarios)

        try:
            resultado = rag.responder(pergunta, usuario)
        except Exception as e:
            print(f"[{i+1:>3}] ERRO: {e}")
            time.sleep(args.pausa)
            continue

        marcador = {"rag": "ok ", "fora_de_escopo": "esc", "erro": "err"}.get(resultado.rota, "?  ")
        if resultado.sem_contexto:
            marcador = "sem"
        score = f"{resultado.score_maximo:.3f}" if resultado.score_maximo is not None else "  -  "
        print(f"[{i+1:>3}] {marcador} score={score}  {pergunta[:52]}")

        if not args.sem_avaliacao and resultado.rota == "rag" and not resultado.sem_contexto:
            evaluation.avaliar(
                resultado.interacao_id, pergunta, resultado.resposta, resultado.chunks
            )

        # Feedback humano simulado, correlacionado com a confianca da
        # recuperacao: score alto tende a gerar 👍. Nem todo mundo vota.
        if resultado.rota == "rag" and random.random() < 0.55:
            base = resultado.score_maximo or 0.0
            util = random.random() < (0.30 if resultado.sem_contexto else min(0.95, base + 0.15))
            db.registrar_feedback(resultado.interacao_id, util)

        time.sleep(args.pausa)

    print("\nPronto. Confira as views:")
    print("  SELECT * FROM vw_kpis_diarios;")
    print("  SELECT * FROM vw_categorias;")
    print("  SELECT * FROM vw_lacunas WHERE NOT resolvida;")
    db.fechar_pool()


if __name__ == "__main__":
    main()
