"""Bot do Telegram.

Local: polling (nao precisa de URL publica).
VPS:   webhook (defina TELEGRAM_WEBHOOK_URL).

Cada resposta vem com botoes de feedback. Esse 👍/👎 e a unica metrica
humana do sistema -- todo o resto e o modelo julgando a si mesmo.
"""
from __future__ import annotations

import asyncio
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import db, evaluation, rag
from .config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

LIMITE_TELEGRAM = 4096

BOAS_VINDAS = (
    "Opa! Eu sou o *Henri* -- nome em homenagem a Henri Nestle, fundador da "
    "marca (bot nao-oficial, projeto pessoal sem vinculo com a Nestle).\n\n"
    "Respondo duvidas sobre o Programa de Trainee da Nestle com base em "
    "documentos e comunicados oficiais -- e cito a fonte de cada resposta.\n\n"
    "Pode perguntar sobre:\n"
    "- etapas e prazos do processo\n"
    "- requisitos de inscricao\n"
    "- beneficios e remuneracao\n"
    "- localidades e mudanca de cidade\n\n"
    "Manda sua pergunta."
)

AJUDA = (
    "*Comandos*\n"
    "/start - apresentacao\n"
    "/ajuda - esta mensagem\n"
    "/sobre - como o bot funciona por dentro\n\n"
    "E so escrever a pergunta normalmente."
)

SOBRE = (
    "*Como eu funciono*\n\n"
    "Arquitetura RAG (Retrieval-Augmented Generation):\n\n"
    "1. Sua pergunta e classificada por um modelo rapido\n"
    "2. Busca hibrida no Postgres: vetorial (pgvector) + lexical (tsvector), "
    "fundidas por Reciprocal Rank Fusion\n"
    "3. Se nenhum trecho passa do limiar de confianca, eu admito que nao sei "
    "em vez de inventar\n"
    "4. A resposta e gerada pelo Llama 3.3 70B via Groq, restrita aos trechos "
    "recuperados\n"
    "5. Um segundo modelo julga a resposta e as notas vao para um dashboard\n\n"
    "Nao guardo seu ID do Telegram: ele e hasheado antes de ir para o banco.\n\n"
    "Meu nome e uma homenagem a Henri Nestle, fundador da marca -- nao sou um "
    "canal oficial da Nestle.\n\n"
    "Codigo aberto -- feito por um candidato, para candidatos."
)


def _botoes_feedback(interacao_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 Ajudou", callback_data=f"fb:1:{interacao_id}"),
        InlineKeyboardButton("👎 Nao ajudou", callback_data=f"fb:0:{interacao_id}"),
    ]])


def _formatar_rodape(resultado: rag.ResultadoRAG) -> str:
    """Transparencia: mostra a confianca da recuperacao ao usuario."""
    if resultado.rota != "rag" or resultado.sem_contexto or resultado.score_maximo is None:
        return ""
    if resultado.score_maximo >= 0.80:
        selo = "🟢 alta confianca"
    elif resultado.score_maximo >= 0.70:
        selo = "🟡 confianca media"
    else:
        selo = "🟠 confianca baixa, confirme na fonte oficial"
    return f"\n\n_{selo}_"


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(BOAS_VINDAS, parse_mode=ParseMode.MARKDOWN)


async def cmd_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(AJUDA, parse_mode=ParseMode.MARKDOWN)


async def cmd_sobre(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(SOBRE, parse_mode=ParseMode.MARKDOWN)


async def tratar_mensagem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    pergunta = (update.message.text or "").strip()
    if not pergunta:
        return
    if len(pergunta) > 1000:
        await update.message.reply_text("Essa pergunta ficou longa demais. Resume um pouco?")
        return

    usuario_hash = db.hashear_usuario(update.effective_user.id)
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    # O pipeline e sincrono (psycopg + groq). Roda em thread para nao
    # travar o event loop do bot.
    resultado = await asyncio.to_thread(rag.responder, pergunta, usuario_hash)

    texto = (resultado.resposta + _formatar_rodape(resultado))[:LIMITE_TELEGRAM]
    teclado = _botoes_feedback(resultado.interacao_id) if resultado.rota == "rag" else None

    await update.message.reply_text(
        texto,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=teclado,
        disable_web_page_preview=True,
    )

    if resultado.rota == "rag" and not resultado.sem_contexto:
        evaluation.avaliar_em_background(
            resultado.interacao_id, pergunta, resultado.resposta, resultado.chunks
        )


async def tratar_feedback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, valor, interacao_id = query.data.split(":")
        util = valor == "1"
        await asyncio.to_thread(db.registrar_feedback, int(interacao_id), util)
        if not util:
            await asyncio.to_thread(
                db.registrar_lacuna, int(interacao_id), query.message.text[:500],
                "feedback_negativo",
            )
    except Exception:
        log.exception("Falha ao registrar feedback: %s", query.data)
        return

    marcador = "👍 Obrigado pelo retorno!" if util else "👎 Anotado, vou melhorar essa parte."
    await query.edit_message_reply_markup(
        InlineKeyboardMarkup([[InlineKeyboardButton(marcador, callback_data="fb:noop")]])
    )


async def tratar_erro(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Erro nao tratado", exc_info=ctx.error)


def main() -> None:
    app = Application.builder().token(config.telegram_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ajuda", cmd_ajuda))
    app.add_handler(CommandHandler("sobre", cmd_sobre))
    app.add_handler(CallbackQueryHandler(tratar_feedback, pattern=r"^fb:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tratar_mensagem))
    app.add_error_handler(tratar_erro)

    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()
    if webhook_url:
        porta = int(os.getenv("PORTA_WEBHOOK", "8080"))
        log.info("Iniciando em modo webhook: %s", webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=porta,
            url_path=config.telegram_token,
            webhook_url=f"{webhook_url}/{config.telegram_token}",
        )
    else:
        log.info("Iniciando em modo polling (desenvolvimento local).")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
