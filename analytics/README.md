# Dashboard — guia de montagem (Metabase)

O Metabase conecta direto no Postgres e lê **apenas as views** de `views.sql`.
Nunca aponte perguntas/dashboards pras tabelas cruas: se o schema mudar, o
dashboard quebra junto.

Roda no mesmo `docker-compose.yml` do projeto, atrás de um profile (não sobe
com `docker compose up -d` normal):

```bash
docker compose --profile analytics up -d metabase
```

Abra `http://localhost:3030` (ou a porta de `PORTA_METABASE` no seu `.env`).

---

## 1. Setup inicial e conexão

Na primeira vez, o Metabase pede um usuário admin local (fica salvo no
próprio banco dele, dentro do volume `metabase_data` — não é uma conta na
nuvem). Em **Adicionar seus dados**, escolha **PostgreSQL** e preencha:

| Campo | Valor |
|---|---|
| Host | `postgres` (nome do serviço no compose — Metabase está na mesma rede Docker, não precisa do IP nem da porta exposta no host) |
| Porta | `5432` |
| Banco de dados | `nestbot` |
| Usuário / senha | os do seu `.env` (`POSTGRES_USER` / `POSTGRES_PASSWORD`) |

Depois de conectar, o Metabase sincroniza o schema sozinho — as 6 views
(`vw_interacoes`, `vw_kpis_diarios`, `vw_categorias`, `vw_cobertura_base`,
`vw_lacunas`, `vw_funil_qualidade`) aparecem como "tabelas" navegáveis em
**Procurar dados**.

> **VPS**: quando for expor isso pra internet (link público de verdade),
> coloque o Metabase atrás de um proxy HTTPS (ex: Caddy/nginx) em vez de
> publicar a porta 3000 direto — vale mencionar essa escolha na entrevista,
> segurança de dados conta ponto.

---

## 2. Perguntas (Questions)

Cada gráfico/cartão do dashboard começa como uma **Question**. Pra tudo que
precisar de SQL um pouco mais elaborado (funil, comparação temporal), use o
**editor SQL nativo** (ícone `>_` em "Nova pergunta") — as consultas da
seção 6 abaixo já servem de ponto de partida direto, cole e ajuste.

Pra cartões simples (contagens, médias), o **construtor visual** resolve sem
SQL: escolha a view, a métrica (`Contar`, `Média de...`) e o agrupamento
(ex: por dia, usando o campo `data`).

Dica de agrupamento temporal: o Metabase já entende datas nativamente (bucket
por dia/semana/mês na própria UI) — não precisa criar uma tabela calendário
à parte como no Power BI.

---

## 3. Métricas por página

Não existe DAX aqui — cada "medida" abaixo é uma Question separada (SQL nativo
ou construtor visual), com a lógica equivalente:

```sql
-- Volume
SELECT COUNT(*) AS total_interacoes FROM vw_interacoes;
SELECT COUNT(DISTINCT usuario_hash) AS usuarios_unicos FROM vw_interacoes;

-- Qualidade da recuperação
SELECT AVG(score_medio) AS score_medio,
       AVG(faithfulness) AS faithfulness,
       AVG(relevancia_resposta) AS relevancia_resposta,
       AVG(relevancia_contexto) AS relevancia_contexto
  FROM vw_interacoes;

SELECT 100.0 * COUNT(*) FILTER (WHERE sem_contexto)
       / NULLIF(COUNT(*), 0) AS taxa_sem_contexto_pct
  FROM vw_interacoes;

SELECT 100.0 * COUNT(*) FILTER (WHERE score_maximo >= 0.80)
       / NULLIF(COUNT(*), 0) AS taxa_alta_confianca_pct
  FROM vw_interacoes;

-- Feedback humano
SELECT 100.0 * COUNT(*) FILTER (WHERE feedback_positivo)
       / NULLIF(COUNT(*) FILTER (WHERE feedback_positivo IS NOT NULL), 0) AS satisfacao_pct
  FROM vw_interacoes;

SELECT 100.0 * COUNT(*) FILTER (WHERE feedback_positivo IS NOT NULL)
       / NULLIF(COUNT(*), 0) AS taxa_engajamento_feedback_pct
  FROM vw_interacoes;

-- Performance
SELECT AVG(latencia_total_ms) / 1000.0 AS latencia_media_s,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latencia_total_ms) / 1000.0 AS latencia_p95_s
  FROM vw_interacoes;

SELECT 100.0 * SUM(latencia_retrieval_ms) / NULLIF(SUM(latencia_total_ms), 0) AS pct_tempo_retrieval
  FROM vw_interacoes;

-- Custo
SELECT SUM(tokens_entrada) + SUM(tokens_saida) AS tokens_totais,
       (SUM(tokens_entrada) + SUM(tokens_saida))::float / NULLIF(COUNT(*), 0) AS tokens_por_resposta
  FROM vw_interacoes;

-- Cobertura da base
SELECT COUNT(*) FILTER (WHERE chunk_orfao) AS chunks_orfaos,
       100.0 * COUNT(*) FILTER (WHERE vezes_recuperado > 0) / NULLIF(COUNT(*), 0) AS cobertura_base_pct
  FROM vw_cobertura_base;

-- Comparação temporal: prova de melhoria contínua
-- (compare a media de "faithfulness" nos ultimos 7 dias vs os 7 dias anteriores)
SELECT AVG(faithfulness) FILTER (WHERE criado_em >= now() - interval '7 days') AS faithfulness_atual,
       AVG(faithfulness) FILTER (WHERE criado_em < now() - interval '7 days'
                                    AND criado_em >= now() - interval '14 days') AS faithfulness_anterior
  FROM vw_interacoes;
```

Pros cartões numéricos com meta (ex: faithfulness ≥ 0,90), use **Visualização
→ Medidor/Progresso** ou formatação condicional por faixa de valor — o
Metabase tem isso nativo em "Formatação" da coluna, sem precisar de DAX.

---

## 4. As quatro páginas (Dashboard com abas)

Crie um **Dashboard** novo, adicione as Questions da seção 3, e use as
**abas** do Metabase (ícone `+` ao lado do nome do dashboard) pra separar em
4 seções — a UI é diferente do Power BI, mas o resultado final é o mesmo
layout:

### Aba 1 — Visão geral
Cartões: Total de interações, Usuários únicos, Satisfação %, Latência média.
Gráfico de linha: interações por dia (`vw_interacoes`, agrupado por `data`).
Gráfico de barras: `vw_categorias[total_perguntas]` por categoria.
Filtro do dashboard: intervalo de datas, ligado a todos os cartões da aba.

### Aba 2 — Qualidade do RAG
O painel principal. Mostra que o sistema é medido, não só construído.

- Medidor: Faithfulness (meta 0,90 — abaixo disso o problema costuma estar
  na recuperação, não na geração)
- Linha: faithfulness e score médio ao longo do tempo
- Histograma: distribuição de `score_maximo` em faixas
- Funil ou barras empilhadas: `vw_funil_qualidade` — alta / média / baixa
  confiança
- Cartão com formatação condicional: Taxa Sem Contexto %
  (vermelho acima de 20%, amarelo entre 10 e 20, verde abaixo de 10)
- Tabela: interações com `resposta_problematica = TRUE`

### Aba 3 — Cobertura da base
- Cartões: Cobertura Base %, Chunks Órfãos
- Barras: `vezes_recuperado` por documento
- Tabela: chunks órfãos, com a prévia do conteúdo

Chunk órfão é conteúdo morto: ou está mal escrito, ou ninguém pergunta sobre
aquilo. Nos dois casos, é sinal de ação.

### Aba 4 — Lacunas
- Tabela: `vw_lacunas` filtrada por `resolvida = FALSE`
- Barras: contagem por `motivo`
- Linha: lacunas abertas ao longo do tempo (a linha deve **cair** conforme
  você alimenta a base)

Esta é a página que fecha o ciclo: lacuna → documento novo → reingestão →
faithfulness sobe na aba 2.

---

## 5. Publicar (link público)

**Admin → Configurações → Compartilhamento público** — ative "Habilitar
compartilhamento público de perguntas e dashboards".

Depois, abra o dashboard e clique no ícone de compartilhar → **Criar um link
público**. Isso gera uma URL que qualquer pessoa acessa sem login — o
equivalente ao "Publish to Web" do Power BI. Como o Metabase hoje só publica
a porta em `127.0.0.1`, esse link **só funciona enquanto acessado da própria
máquina** (ou depois que o Metabase estiver rodando na VPS, com a porta
exposta atrás do proxy).

Enquanto a VPS não sai do papel: exporte o dashboard como PDF/imagem
(**Exportar como PDF** no próprio dashboard) e versione no repositório — o
avaliador vê o resultado sem precisar de acesso ao banco nem à rede.

Dado real não some entre reinícios: o Metabase guarda perguntas e dashboards
no arquivo H2 dentro do volume `metabase_data`, que persiste no Docker
mesmo depois de um `docker compose down` (some só com `down -v`).

---

## 6. Consultas úteis para conferir os dados

```sql
-- números de capa
SELECT * FROM vw_funil_qualidade;

-- evolução diária
SELECT * FROM vw_kpis_diarios ORDER BY data DESC;

-- onde o RAG mais falha
SELECT categoria, total_perguntas, taxa_sem_contexto_pct, faithfulness_media
  FROM vw_categorias
 ORDER BY taxa_sem_contexto_pct DESC;

-- conteúdo morto na base
SELECT documento, ordem, previa
  FROM vw_cobertura_base
 WHERE chunk_orfao
 ORDER BY documento, ordem;

-- backlog de melhoria
SELECT motivo, COUNT(*) AS total
  FROM vw_lacunas
 WHERE NOT resolvida
 GROUP BY motivo
 ORDER BY total DESC;
```
