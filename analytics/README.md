# Dashboard — guia de montagem

O Power BI conecta direto no Postgres e lê **apenas as views** de `views.sql`.
Nunca aponte o dashboard para as tabelas cruas: se o schema mudar, o `.pbix`
quebra junto.

---

## 1. Conectar

**Obter Dados → Banco de Dados PostgreSQL**

| Campo | Valor |
|---|---|
| Servidor | `localhost:5432` (local) ou `IP_DA_VPS:5432` |
| Banco de dados | `nestbot` |
| Modo | **Importar** |

Usuário e senha são os do `.env`.

> **Driver**: o conector do Power BI precisa do *Npgsql*. Se der erro de provedor
> ausente, instale o Npgsql com a opção "GAC" marcada e reinicie o Power BI.

> **VPS**: não exponha a porta 5432 na internet aberta. Use um túnel SSH
> (`ssh -L 5432:localhost:5432 usuario@ip-da-vps`) e conecte em `localhost`.
> Vale mencionar essa escolha na entrevista — segurança de dados conta ponto.

Selecione: `vw_interacoes`, `vw_kpis_diarios`, `vw_categorias`,
`vw_cobertura_base`, `vw_lacunas`, `vw_funil_qualidade`.

---

## 2. Tabela calendário

Sem uma dimensão de data, análise temporal em Power BI vira gambiarra. Crie uma
tabela via DAX:

```dax
dCalendario =
ADDCOLUMNS(
    CALENDAR( MIN(vw_interacoes[data]), MAX(vw_interacoes[data]) ),
    "Ano",      YEAR([Date]),
    "Mes",      FORMAT([Date], "MMM"),
    "MesNum",   MONTH([Date]),
    "Semana",   WEEKNUM([Date]),
    "DiaSemana", FORMAT([Date], "ddd")
)
```

Marque como tabela de datas e relacione `dCalendario[Date]` → `vw_interacoes[data]`
(1:N, direção única).

---

## 3. Medidas DAX

```dax
-- Volume
Total Interacoes = COUNTROWS(vw_interacoes)
Usuarios Unicos  = DISTINCTCOUNT(vw_interacoes[usuario_hash])

-- Qualidade da recuperação
Score Medio      = AVERAGE(vw_interacoes[score_medio])
Faithfulness     = AVERAGE(vw_interacoes[faithfulness])
Relev. Resposta  = AVERAGE(vw_interacoes[relevancia_resposta])
Relev. Contexto  = AVERAGE(vw_interacoes[relevancia_contexto])

Taxa Sem Contexto % =
DIVIDE(
    CALCULATE( COUNTROWS(vw_interacoes), vw_interacoes[sem_contexto] = TRUE() ),
    [Total Interacoes]
) * 100

Taxa Alta Confianca % =
DIVIDE(
    CALCULATE( COUNTROWS(vw_interacoes), vw_interacoes[score_maximo] >= 0.80 ),
    [Total Interacoes]
) * 100

-- Feedback humano
Satisfacao % =
VAR Positivos = CALCULATE( COUNTROWS(vw_interacoes), vw_interacoes[feedback_positivo] = TRUE() )
VAR ComVoto   = CALCULATE( COUNTROWS(vw_interacoes), NOT ISBLANK(vw_interacoes[feedback_positivo]) )
RETURN DIVIDE(Positivos, ComVoto) * 100

Taxa Engajamento Feedback % =
DIVIDE(
    CALCULATE( COUNTROWS(vw_interacoes), NOT ISBLANK(vw_interacoes[feedback_positivo]) ),
    [Total Interacoes]
) * 100

-- Performance
Latencia Media (s)   = AVERAGE(vw_interacoes[latencia_total_ms]) / 1000
Latencia P95 (s)     = PERCENTILEX.INC(vw_interacoes, vw_interacoes[latencia_total_ms], 0.95) / 1000
% Tempo em Retrieval =
DIVIDE( SUM(vw_interacoes[latencia_retrieval_ms]), SUM(vw_interacoes[latencia_total_ms]) ) * 100

-- Custo
Tokens Totais = SUM(vw_interacoes[tokens_entrada]) + SUM(vw_interacoes[tokens_saida])
Tokens por Resposta = DIVIDE([Tokens Totais], [Total Interacoes])

-- Cobertura da base
Chunks Orfaos = CALCULATE( COUNTROWS(vw_cobertura_base), vw_cobertura_base[chunk_orfao] = TRUE() )
Cobertura Base % =
DIVIDE(
    CALCULATE( COUNTROWS(vw_cobertura_base), vw_cobertura_base[vezes_recuperado] > 0 ),
    COUNTROWS(vw_cobertura_base)
) * 100

-- Comparação temporal: é a medida que prova melhoria contínua
Faithfulness Periodo Anterior =
CALCULATE( [Faithfulness], DATEADD(dCalendario[Date], -7, DAY) )

Delta Faithfulness = [Faithfulness] - [Faithfulness Periodo Anterior]
```

---

## 4. As quatro páginas

### Página 1 — Visão geral
Cartões: `Total Interacoes`, `Usuarios Unicos`, `Satisfacao %`, `Latencia Media (s)`.
Gráfico de linha: interações por dia.
Gráfico de barras: `vw_categorias[total_perguntas]` por categoria.
Segmentação: `dCalendario[Date]`.

### Página 2 — Qualidade do RAG
O painel principal. Mostra que o sistema é medido, não só construído.

- Medidor: `Faithfulness` (meta 0,90 — abaixo disso o problema costuma estar na
  recuperação, não na geração)
- Linha: faithfulness e score médio ao longo do tempo, com `Delta Faithfulness`
- Histograma: distribuição de `score_maximo` em faixas
- Funil: `vw_funil_qualidade` — alta / média / baixa confiança
- Cartão com destaque condicional: `Taxa Sem Contexto %`
  (vermelho acima de 20%, amarelo entre 10 e 20, verde abaixo de 10)
- Tabela: interações com `resposta_problematica = TRUE`

### Página 3 — Cobertura da base
- Cartões: `Cobertura Base %`, `Chunks Orfaos`
- Barras: `vezes_recuperado` por documento
- Tabela: chunks órfãos, com a prévia do conteúdo

Chunk órfão é conteúdo morto: ou está mal escrito, ou ninguém pergunta sobre
aquilo. Nos dois casos, é sinal de ação.

### Página 4 — Lacunas
- Tabela: `vw_lacunas` filtrada por `resolvida = FALSE`
- Barras: contagem por `motivo`
- Linha: lacunas abertas ao longo do tempo (a linha deve **cair** conforme você
  alimenta a base)

Esta é a página que fecha o ciclo: lacuna → documento novo → reingestão →
faithfulness sobe na página 2.

---

## 5. Atualização

Local: **Atualizar** manualmente antes de apresentar.
Publicado no Power BI Service: precisa de gateway para alcançar o Postgres.
Para portfólio, exportar as páginas em PDF/imagem e versionar no repositório já
resolve — o avaliador vê o resultado sem precisar de acesso ao banco.

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
