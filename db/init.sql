-- ============================================================
-- NestBot :: schema inicial
-- Postgres 16 + pgvector
-- Duas metades: (1) base de conhecimento do RAG
--               (2) telemetria para avaliar o proprio RAG
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ------------------------------------------------------------
-- 1. BASE DE CONHECIMENTO
-- ------------------------------------------------------------

CREATE TABLE documentos (
    id              BIGSERIAL PRIMARY KEY,
    titulo          TEXT        NOT NULL,
    fonte           TEXT        NOT NULL,           -- ex: 'nestle.com.br/carreiras'
    url             TEXT,
    categoria       TEXT        NOT NULL,           -- beneficios | etapas | requisitos | localidades | institucional
    hash_conteudo   TEXT        NOT NULL UNIQUE,    -- evita reingestao do mesmo conteudo
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE documentos IS 'Documento bruto ingerido antes do chunking.';

CREATE TABLE chunks (
    id                  BIGSERIAL PRIMARY KEY,
    documento_id        BIGINT      NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    ordem               INT         NOT NULL,       -- posicao do chunk dentro do documento
    conteudo            TEXT        NOT NULL,
    n_caracteres        INT         NOT NULL,
    embedding           VECTOR(384) NOT NULL,       -- multilingual-e5-small = 384 dims
    modelo_embedding    TEXT        NOT NULL,
    -- coluna gerada para busca lexical em portugues (metade "keyword" do hibrido)
    tsv                 TSVECTOR GENERATED ALWAYS AS (to_tsvector('portuguese', conteudo)) STORED,
    -- contadores alimentados em tempo de execucao -> viram KPI no Power BI
    vezes_recuperado    INT         NOT NULL DEFAULT 0,
    soma_scores         DOUBLE PRECISION NOT NULL DEFAULT 0,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (documento_id, ordem)
);

COMMENT ON COLUMN chunks.soma_scores IS 'Soma dos scores de similaridade; dividida por vezes_recuperado da o score medio.';

-- HNSW para busca vetorial (cosine). Melhor recall/latencia que IVFFlat em bases pequenas.
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_tsv       ON chunks USING gin (tsv);
CREATE INDEX idx_chunks_documento ON chunks (documento_id);

-- ------------------------------------------------------------
-- 2. TELEMETRIA / OBSERVABILIDADE DO RAG
-- ------------------------------------------------------------

CREATE TABLE interacoes (
    id                      BIGSERIAL PRIMARY KEY,
    -- id do usuario e sempre hasheado: nao guardamos identificador do Telegram em claro
    usuario_hash            TEXT        NOT NULL,
    pergunta                TEXT        NOT NULL,
    categoria_detectada     TEXT,                   -- classificada pelo LLM roteador
    rota                    TEXT        NOT NULL,   -- rag | fora_de_escopo | erro
    resposta                TEXT,

    -- o que a recuperacao trouxe (auditavel depois)
    chunks_recuperados      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    n_chunks                INT         NOT NULL DEFAULT 0,
    score_medio             DOUBLE PRECISION,
    score_maximo            DOUBLE PRECISION,
    sem_contexto            BOOLEAN     NOT NULL DEFAULT FALSE,  -- nada acima do threshold

    -- performance
    latencia_retrieval_ms   INT,
    latencia_geracao_ms     INT,
    latencia_total_ms       INT,

    modelo_geracao          TEXT,
    tokens_entrada          INT,
    tokens_saida            INT,

    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_interacoes_criado_em  ON interacoes (criado_em DESC);
CREATE INDEX idx_interacoes_categoria  ON interacoes (categoria_detectada);
CREATE INDEX idx_interacoes_usuario    ON interacoes (usuario_hash);

-- Avaliacao automatica (LLM-as-judge, no espirito do RAGAS)
CREATE TABLE avaliacoes (
    id                  BIGSERIAL PRIMARY KEY,
    interacao_id        BIGINT      NOT NULL REFERENCES interacoes(id) ON DELETE CASCADE,
    faithfulness        DOUBLE PRECISION,   -- 0..1 a resposta se sustenta no contexto?
    relevancia_resposta DOUBLE PRECISION,   -- 0..1 a resposta responde a pergunta?
    relevancia_contexto DOUBLE PRECISION,   -- 0..1 os chunks eram pertinentes?
    justificativa       TEXT,
    modelo_avaliador    TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (interacao_id)
);

CREATE INDEX idx_avaliacoes_interacao ON avaliacoes (interacao_id);

-- Feedback humano (botoes no Telegram)
CREATE TABLE feedback (
    id              BIGSERIAL PRIMARY KEY,
    interacao_id    BIGINT      NOT NULL REFERENCES interacoes(id) ON DELETE CASCADE,
    util            BOOLEAN     NOT NULL,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (interacao_id)
);

-- Lacunas: perguntas que o RAG nao conseguiu responder.
-- E o insumo de melhoria continua da base -> painel dedicado no Power BI.
CREATE TABLE lacunas_conhecimento (
    id              BIGSERIAL PRIMARY KEY,
    interacao_id    BIGINT      REFERENCES interacoes(id) ON DELETE SET NULL,
    pergunta        TEXT        NOT NULL,
    motivo          TEXT        NOT NULL,   -- sem_contexto | score_baixo | feedback_negativo | faithfulness_baixa
    resolvida       BOOLEAN     NOT NULL DEFAULT FALSE,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_lacunas_resolvida ON lacunas_conhecimento (resolvida, criado_em DESC);
