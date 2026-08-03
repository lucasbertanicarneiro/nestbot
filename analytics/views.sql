-- ============================================================
-- NestBot :: camada analitica
-- O Power BI le SOMENTE estas views (nunca as tabelas cruas).
-- Motivo: desacopla o dashboard do schema; se o schema mudar,
-- so a view muda e o .pbix continua funcionando.
-- ============================================================

-- ------------------------------------------------------------
-- vw_interacoes :: tabela fato principal
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_interacoes AS
SELECT
    i.id,
    i.criado_em,
    i.criado_em::date                       AS data,
    EXTRACT(HOUR FROM i.criado_em)::int     AS hora,
    TO_CHAR(i.criado_em, 'Day')             AS dia_semana,
    i.usuario_hash,
    i.pergunta,
    COALESCE(i.categoria_detectada, 'nao_classificada') AS categoria,
    i.rota,
    i.n_chunks,
    i.score_medio,
    i.score_maximo,
    i.sem_contexto,
    i.latencia_retrieval_ms,
    i.latencia_geracao_ms,
    i.latencia_total_ms,
    i.modelo_geracao,
    i.tokens_entrada,
    i.tokens_saida,
    a.faithfulness,
    a.relevancia_resposta,
    a.relevancia_contexto,
    f.util                                  AS feedback_positivo,
    -- flag de qualidade: usada nos cartoes de "resposta problematica"
    CASE
        WHEN i.sem_contexto THEN TRUE
        WHEN a.faithfulness IS NOT NULL AND a.faithfulness < 0.7 THEN TRUE
        WHEN f.util IS FALSE THEN TRUE
        ELSE FALSE
    END                                     AS resposta_problematica
FROM interacoes i
LEFT JOIN avaliacoes a ON a.interacao_id = i.id
LEFT JOIN feedback   f ON f.interacao_id = i.id;

-- ------------------------------------------------------------
-- vw_kpis_diarios :: linha do tempo de qualidade
-- E o painel que mostra se a base MELHOROU depois dos ajustes.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_kpis_diarios AS
SELECT
    i.criado_em::date                                       AS data,
    COUNT(*)                                                AS total_interacoes,
    COUNT(DISTINCT i.usuario_hash)                          AS usuarios_unicos,
    ROUND(AVG(i.score_medio)::numeric, 4)                   AS score_similaridade_medio,
    ROUND(AVG(i.latencia_total_ms)::numeric, 0)             AS latencia_media_ms,
    ROUND(
        (COUNT(*) FILTER (WHERE i.sem_contexto)::numeric / NULLIF(COUNT(*), 0)) * 100
    , 2)                                                    AS taxa_sem_contexto_pct,
    ROUND(AVG(a.faithfulness)::numeric, 4)                  AS faithfulness_media,
    ROUND(AVG(a.relevancia_resposta)::numeric, 4)           AS relevancia_resposta_media,
    ROUND(AVG(a.relevancia_contexto)::numeric, 4)           AS relevancia_contexto_media,
    COUNT(f.id) FILTER (WHERE f.util)                       AS feedback_positivo,
    COUNT(f.id) FILTER (WHERE NOT f.util)                   AS feedback_negativo,
    ROUND(
        (COUNT(f.id) FILTER (WHERE f.util)::numeric / NULLIF(COUNT(f.id), 0)) * 100
    , 2)                                                    AS satisfacao_pct,
    SUM(i.tokens_entrada + i.tokens_saida)                  AS tokens_consumidos
FROM interacoes i
LEFT JOIN avaliacoes a ON a.interacao_id = i.id
LEFT JOIN feedback   f ON f.interacao_id = i.id
GROUP BY i.criado_em::date;

-- ------------------------------------------------------------
-- vw_categorias :: onde estao as duvidas dos candidatos
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_categorias AS
SELECT
    COALESCE(i.categoria_detectada, 'nao_classificada')     AS categoria,
    COUNT(*)                                                AS total_perguntas,
    ROUND(AVG(i.score_medio)::numeric, 4)                   AS score_medio,
    ROUND(AVG(a.faithfulness)::numeric, 4)                  AS faithfulness_media,
    ROUND(
        (COUNT(*) FILTER (WHERE i.sem_contexto)::numeric / NULLIF(COUNT(*), 0)) * 100
    , 2)                                                    AS taxa_sem_contexto_pct,
    ROUND(AVG(i.latencia_total_ms)::numeric, 0)             AS latencia_media_ms
FROM interacoes i
LEFT JOIN avaliacoes a ON a.interacao_id = i.id
GROUP BY 1;

-- ------------------------------------------------------------
-- vw_cobertura_base :: quais chunks a base realmente usa
-- Chunk com vezes_recuperado = 0 e conteudo morto:
-- ou esta mal escrito, ou ninguem pergunta sobre aquilo.
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_cobertura_base AS
SELECT
    c.id                                                    AS chunk_id,
    d.titulo                                                AS documento,
    d.categoria,
    d.fonte,
    c.ordem,
    LEFT(c.conteudo, 160)                                   AS previa,
    c.n_caracteres,
    c.vezes_recuperado,
    CASE WHEN c.vezes_recuperado > 0
         THEN ROUND((c.soma_scores / c.vezes_recuperado)::numeric, 4)
    END                                                     AS score_medio_quando_recuperado,
    (c.vezes_recuperado = 0)                                AS chunk_orfao
FROM chunks c
JOIN documentos d ON d.id = c.documento_id;

-- ------------------------------------------------------------
-- vw_lacunas :: backlog de melhoria da base de conhecimento
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_lacunas AS
SELECT
    l.id,
    l.criado_em,
    l.criado_em::date   AS data,
    l.pergunta,
    l.motivo,
    l.resolvida,
    i.categoria_detectada AS categoria,
    i.score_maximo
FROM lacunas_conhecimento l
LEFT JOIN interacoes i ON i.id = l.interacao_id;

-- ------------------------------------------------------------
-- vw_funil_qualidade :: numeros de capa do dashboard
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW vw_funil_qualidade AS
SELECT
    COUNT(*)                                                        AS total,
    COUNT(*) FILTER (WHERE NOT sem_contexto)                        AS com_contexto,
    COUNT(*) FILTER (WHERE rota = 'rag')                            AS respondidas,
    COUNT(*) FILTER (WHERE rota = 'erro')                           AS com_erro,
    COUNT(*) FILTER (WHERE score_maximo >= 0.80)                    AS alta_confianca,
    COUNT(*) FILTER (WHERE score_maximo BETWEEN 0.60 AND 0.7999)    AS media_confianca,
    COUNT(*) FILTER (WHERE score_maximo < 0.60)                     AS baixa_confianca
FROM interacoes;
