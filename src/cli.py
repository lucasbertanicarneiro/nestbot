"""CLI de teste. Permite validar o RAG antes de ligar o Telegram.

    python -m src.cli                          # modo interativo
    python -m src.cli -p "qual o prazo?"       # pergunta unica
    python -m src.cli --diagnostico            # so mostra a recuperacao
"""
from __future__ import annotations

import argparse
import logging

from . import db, evaluation, rag
from .retrieval import recuperar

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

USUARIO_TESTE = db.hashear_usuario("cli-local")


def _diagnosticar(pergunta: str) -> None:
    """Mostra o que a recuperacao trouxe, sem gerar resposta.
    E a ferramenta para calibrar chunking e limiar."""
    chunks = recuperar(pergunta)
    if not chunks:
        print("Nenhum chunk recuperado.")
        return
    print(f"\n{len(chunks)} chunk(s) para: {pergunta!r}\n")
    for i, c in enumerate(chunks, 1):
        print(f"[{i}] score={c.score:.4f}  rrf={c.score_rrf:.5f}  origem={c.origem}")
        print(f"    doc: {c.documento}  ({c.categoria})")
        print(f"    {c.conteudo[:220].replace(chr(10), ' ')}...\n")


def _perguntar(pergunta: str, avaliar: bool) -> None:
    resultado = rag.responder(pergunta, USUARIO_TESTE)

    print("\n" + "=" * 70)
    print(resultado.resposta)
    print("=" * 70)
    print(
        f"rota={resultado.rota}  categoria={resultado.categoria}  "
        f"chunks={len(resultado.chunks)}  "
        f"score_max={resultado.score_maximo:.4f}" if resultado.score_maximo is not None
        else f"rota={resultado.rota}  categoria={resultado.categoria}"
    )

    if avaliar and resultado.rota == "rag" and not resultado.sem_contexto:
        print("\nAvaliando...")
        notas = evaluation.avaliar(
            resultado.interacao_id, pergunta, resultado.resposta, resultado.chunks
        )
        if notas:
            print(
                f"  faithfulness       : {notas['faithfulness']}\n"
                f"  relevancia_resposta: {notas['relevancia_resposta']}\n"
                f"  relevancia_contexto: {notas['relevancia_contexto']}\n"
                f"  {notas['justificativa']}"
            )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI de teste do NestBot")
    parser.add_argument("-p", "--pergunta", help="pergunta unica e sai")
    parser.add_argument("--diagnostico", action="store_true", help="so mostra a recuperacao")
    parser.add_argument("--sem-avaliacao", action="store_true", help="pula o LLM-as-judge")
    args = parser.parse_args()

    if args.pergunta:
        if args.diagnostico:
            _diagnosticar(args.pergunta)
        else:
            _perguntar(args.pergunta, avaliar=not args.sem_avaliacao)
        db.fechar_pool()
        return

    print("NestBot CLI. Ctrl+C para sair. Prefixo '?' faz so o diagnostico.\n")
    try:
        while True:
            entrada = input("> ").strip()
            if not entrada:
                continue
            if entrada.startswith("?"):
                _diagnosticar(entrada[1:].strip())
            else:
                _perguntar(entrada, avaliar=not args.sem_avaliacao)
    except (KeyboardInterrupt, EOFError):
        print("\nAte mais.")
    finally:
        db.fechar_pool()


if __name__ == "__main__":
    main()
