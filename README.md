# NestBot

Assistente de Telegram que responde dúvidas de candidatos ao Programa de Trainee
da Nestlé, com arquitetura RAG e um dashboard que mede a qualidade da própria
recuperação.

> Projeto pessoal, sem vínculo com a Nestlé. A base de conhecimento é montada a
> partir de comunicados e páginas públicas.

---

## O problema

Quem se inscreve num programa de trainee tem as mesmas dúvidas repetidas — prazo,
requisitos, benefícios, se vai precisar mudar de cidade — e a resposta está
espalhada por comunicados de imprensa, FAQ e páginas institucionais. Ninguém lê
tudo isso.

Um chatbot resolve a parte fácil. A parte difícil é garantir que ele **não invente
resposta** sobre um processo seletivo real, onde informação errada custa caro para
quem está se candidatando.

Este projeto trata a confiabilidade como requisito de engenharia, não como
detalhe: o sistema mede continuamente se as próprias respostas estão sustentadas
nos documentos, e o dashboard existe para expor isso.

---

## Arquitetura

```
                       pergunta (Telegram)
                              │
                    ┌─────────▼─────────┐
                    │  Roteador (LLM    │   Llama 3.1 8B — rápido e barato
                    │  rápido)          │   classifica categoria + escopo
                    └─────────┬─────────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
     ┌────────▼────────┐            ┌─────────▼────────┐
     │ Busca vetorial  │            │  Busca lexical   │
     │ pgvector HNSW   │            │  tsvector PT-BR  │
     │ (semântica)     │            │  (termo exato)   │
     └────────┬────────┘            └─────────┬────────┘
              └───────────────┬───────────────┘
                    ┌─────────▼─────────┐
                    │  Fusão RRF        │   funde por posição, não por score
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Guarda-corpo     │   score < limiar → admite que não sabe
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Geração          │   Llama 3.3 70B, restrito ao contexto
                    └─────────┬─────────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
     ┌────────▼────────┐            ┌─────────▼────────┐
     │ Resposta ao     │            │  Avaliador       │  LLM-as-judge
     │ usuário + 👍👎  │            │  (assíncrono)    │  faithfulness etc.
     └─────────────────┘            └─────────┬────────┘
                                              │
                                    ┌─────────▼─────────┐
                                    │ Postgres → views  │
                                    │ → Power BI        │
                                    └───────────────────┘
```

### Decisões técnicas e o porquê

| Decisão | Motivo |
|---|---|
| **Busca híbrida (vetorial + lexical)** | Busca semântica pura erra termo exato e entidade rara — "9 de setembro", "Ituiutaba", "AWS Cloud Practitioner". A busca lexical do Postgres cobre esse buraco. |
| **RRF em vez de soma ponderada** | Funde as duas listas pela *posição*, não pelo score. Evita normalizar escalas incompatíveis (distância de cosseno vs `ts_rank`). |
| **pgvector dentro do Postgres** | Um banco só para dado relacional e vetorial. Menos infra, e o dashboard cruza embeddings e telemetria com um `JOIN` comum. |
| **Embeddings locais (`multilingual-e5-small`)** | O Groq não expõe endpoint de embedding. Rodar local dá custo zero, tira a ingestão do rate limit e mantém qualidade boa em português. |
| **Groq para inferência** | Free tier sem cartão e inferência em LPU — latência baixa importa num bot de chat, onde a pessoa está esperando. |
| **Guarda-corpo por limiar** | Se nenhum trecho passa do limiar de confiança, o bot **não gera resposta**. Num contexto de processo seletivo real, "não sei" é melhor que um palpite plausível. |
| **Índice HNSW** | Melhor recall/latência que IVFFlat em bases pequenas, e não exige treino prévio do índice. |
| **Avaliação assíncrona** | O julgamento roda em thread separada: mede qualidade sem custar latência para quem está esperando a resposta. |
| **ID do Telegram hasheado** | Telemetria não precisa de identidade. Hash com sal antes de gravar. |
| **Power BI lê só views** | Desacopla o dashboard do schema. Se as tabelas mudarem, só a view muda e o `.pbix` continua funcionando. |

---

## Stack

- **Python 3.12**
- **PostgreSQL 16** + **pgvector** (busca vetorial) + **tsvector** (busca lexical)
- **Groq API** — Llama 3.3 70B (geração) e Llama 3.1 8B (roteamento e avaliação)
- **sentence-transformers** — `intfloat/multilingual-e5-small`, 384 dimensões
- **python-telegram-bot** — polling em dev, webhook em produção
- **Docker Compose**
- **Power BI** — camada de visualização

---

## Como rodar

Pré-requisitos: Docker e Docker Compose v2.

```bash
git clone <url-do-repo> && cd nestbot
./scripts/setup.sh          # cria o .env e para, pedindo as chaves
# preencha GROQ_API_KEY e TELEGRAM_BOT_TOKEN no .env
./scripts/setup.sh          # roda de novo: sobe o banco, builda e ingere
```

Onde pegar as chaves:
- **Groq**: console.groq.com/keys — free tier, sem cartão
- **Telegram**: fale com o `@BotFather`, comando `/newbot`

### Testando

```bash
# CLI, sem depender do Telegram
docker compose run --rm bot python -m src.cli

# diagnóstico da recuperação (prefixo "?" no modo interativo)
docker compose run --rm bot python -m src.cli -p "qual o prazo?" --diagnostico

# sobe o bot
docker compose up -d bot
docker compose logs -f bot
```

### Populando o dashboard

```bash
docker compose run --rm bot python scripts/simular_uso.py --n 40
```

Roda perguntas reais pelo pipeline completo. A recuperação, a geração e a
avaliação acontecem de fato — nenhum número do dashboard é inventado.

---

## Atualizando a base de conhecimento

Coloque um `.md` em `data/knowledge/` com frontmatter:

```markdown
---
titulo: "Título do documento"
fonte: "Nestlé Brasil - de onde veio"
url: "https://..."
categoria: "beneficios"
---

# Seção

Conteúdo...
```

E rode `docker compose run --rm bot python -m src.ingest`.

A ingestão é idempotente: o hash do conteúdo evita reprocessar documento que não
mudou. Documento alterado é reindexado do zero.

**Chunking contextual**: cada chunk carrega o breadcrumb `[documento > seção]` no
cabeçalho, então continua fazendo sentido quando cai no prompt isolado dos vizinhos.

---

## Dashboard

Ver [`analytics/README.md`](analytics/README.md) para a montagem no Power BI.

Quatro páginas:
1. **Visão geral** — volume, satisfação, latência
2. **Qualidade do RAG** — faithfulness, distribuição de scores, taxa de "não sei"
3. **Cobertura da base** — quais chunks são usados, quais são órfãos
4. **Lacunas** — perguntas que a base não cobre; é o backlog de melhoria

O ciclo é o ponto: as lacunas viram documento novo, a reingestão muda os scores, e
a página de qualidade mostra se melhorou de fato.

---

## Estrutura

```
nestbot/
├── db/init.sql              # schema: conhecimento + telemetria
├── analytics/
│   ├── views.sql            # camada que o Power BI consome
│   └── README.md            # guia de montagem do dashboard
├── src/
│   ├── config.py            # configuração via env
│   ├── db.py                # pool, telemetria, hash de usuário
│   ├── embeddings.py        # sentence-transformers local
│   ├── ingest.py            # chunking + indexação
│   ├── retrieval.py         # busca híbrida + RRF
│   ├── llm.py               # cliente Groq com backoff
│   ├── prompts.py           # prompts versionados
│   ├── rag.py               # orquestração do pipeline
│   ├── evaluation.py        # LLM-as-judge
│   ├── bot.py               # Telegram
│   └── cli.py               # teste sem Telegram
├── scripts/
│   ├── setup.sh
│   └── simular_uso.py
├── data/knowledge/          # base de conhecimento (.md)
├── docker-compose.yml
└── Dockerfile
```

---

## Limitações conhecidas

- **A base cobre só o que é público.** Salário exato, nota de corte e número de
  vagas não são divulgados — o bot responde que não sabe, e isso é o
  comportamento correto.
- **LLM-as-judge não é verdade absoluta.** É um proxy razoável e barato de
  qualidade, não um rótulo humano. Serve para detectar *tendência* (a
  faithfulness caiu depois que mudei o chunking?), não para auditoria.
- **Sem reranker dedicado.** Um cross-encoder melhoraria a precisão do top-k, mas
  adiciona latência e mais um modelo em memória — desproporcional para uma base
  deste tamanho.
- **Informação de edições anteriores.** Alguns dados podem ser de edições
  passadas; o prompt instrui o modelo a sinalizar isso, mas a fonte oficial
  continua sendo a palavra final.

---

## Privacidade

- O ID do Telegram é hasheado com sal antes de ir para o banco; não guardamos
  identificador em claro.
- Perguntas são armazenadas para medir a qualidade do sistema.
- Chaves de API ficam em variáveis de ambiente, nunca no código. O `.env` está
  no `.gitignore`.
