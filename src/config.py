"""Configuracao central. Tudo vem de variavel de ambiente, nada hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(chave: str, padrao: str | None = None, obrigatorio: bool = False) -> str:
    valor = os.getenv(chave, padrao)
    if obrigatorio and not valor:
        raise RuntimeError(
            f"Variavel de ambiente obrigatoria ausente: {chave}. "
            f"Copie .env.example para .env e preencha."
        )
    return valor or ""


@dataclass(frozen=True)
class Config:
    # --- Banco ---
    db_host: str = field(default_factory=lambda: _env("POSTGRES_HOST", "localhost"))
    db_port: int = field(default_factory=lambda: int(_env("POSTGRES_PORT", "5432")))
    db_nome: str = field(default_factory=lambda: _env("POSTGRES_DB", "nestbot"))
    db_usuario: str = field(default_factory=lambda: _env("POSTGRES_USER", "nestbot"))
    db_senha: str = field(default_factory=lambda: _env("POSTGRES_PASSWORD", obrigatorio=True))
    # Quanto esperar por uma conexao livre do pool antes de desistir. Padrao
    # do psycopg_pool e 30s -- tempo demais pro usuario ficar vendo "digitando..."
    # sem resposta quando o banco cai de vez; um valor menor ainda tolera
    # instabilidade passageira de rede sem virar falso positivo.
    db_pool_timeout_seg: float = field(
        default_factory=lambda: float(_env("DB_POOL_TIMEOUT_SEG", "6"))
    )

    # --- Groq ---
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY", obrigatorio=True))
    # Modelo forte: gera a resposta final a partir do contexto recuperado.
    modelo_geracao: str = field(
        default_factory=lambda: _env("GROQ_MODELO_GERACAO", "llama-3.3-70b-versatile")
    )
    # Modelo rapido/barato: classifica intencao e faz o julgamento automatico.
    modelo_rapido: str = field(
        default_factory=lambda: _env("GROQ_MODELO_RAPIDO", "openai/gpt-oss-20b")
    )
    # Modelo com visao: transcreve print/imagem no importador (src/import_web.py).
    modelo_visao: str = field(
        default_factory=lambda: _env("GROQ_MODELO_VISAO", "qwen/qwen3.6-27b")
    )

    # --- Telegram ---
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", obrigatorio=True))

    # --- Embeddings ---
    # Roda local (CPU). Groq nao expoe endpoint de embedding, entao a
    # vetorizacao fica com sentence-transformers -> custo zero e sem rate limit.
    modelo_embedding: str = field(
        default_factory=lambda: _env("MODELO_EMBEDDING", "intfloat/multilingual-e5-small")
    )
    dimensao_embedding: int = 384

    # --- RAG ---
    chunk_tamanho: int = field(default_factory=lambda: int(_env("CHUNK_TAMANHO", "900")))
    chunk_sobreposicao: int = field(default_factory=lambda: int(_env("CHUNK_SOBREPOSICAO", "150")))
    top_k_vetorial: int = field(default_factory=lambda: int(_env("TOP_K_VETORIAL", "10")))
    top_k_lexical: int = field(default_factory=lambda: int(_env("TOP_K_LEXICAL", "10")))
    top_k_final: int = field(default_factory=lambda: int(_env("TOP_K_FINAL", "4")))
    # Abaixo disso consideramos que a base nao cobre a pergunta.
    limiar_similaridade: float = field(
        default_factory=lambda: float(_env("LIMIAR_SIMILARIDADE", "0.62"))
    )
    # Constante do Reciprocal Rank Fusion (valor classico da literatura).
    rrf_k: int = 60

    # --- Memoria de conversa ---
    # Quantas trocas anteriores do mesmo usuario entram no prompt. So a mais
    # recente (default 1) -- com mais de uma, um assunto antigo que se repete
    # na janela vira "maioria" e o roteador tende a puxar de volta pra ele em
    # vez de resolver a pergunta de fechamento mais recente (ex: "sim" voltando
    # sempre pro mesmo assunto de 2-3 trocas atras).
    historico_turnos: int = field(default_factory=lambda: int(_env("HISTORICO_TURNOS", "1")))
    # Trocas mais antigas que isso nao contam mais como a mesma "sessao".
    historico_janela_minutos: int = field(
        default_factory=lambda: int(_env("HISTORICO_JANELA_MINUTOS", "60"))
    )

    # --- Avaliacao ---
    avaliar_automaticamente: bool = field(
        default_factory=lambda: _env("AVALIAR_AUTOMATICAMENTE", "true").lower() == "true"
    )

    # --- Privacidade ---
    # Sal usado para hashear o id do Telegram antes de gravar.
    sal_hash: str = field(default_factory=lambda: _env("SAL_HASH", "nestbot-local"))

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.db_usuario}:{self.db_senha}"
            f"@{self.db_host}:{self.db_port}/{self.db_nome}"
        )


config = Config()
