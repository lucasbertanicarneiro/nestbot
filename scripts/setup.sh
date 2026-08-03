#!/usr/bin/env bash
# Setup local do NestBot. Idempotente: pode rodar quantas vezes quiser.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Verificando pre-requisitos"
command -v docker >/dev/null || { echo "ERRO: Docker nao encontrado."; exit 1; }
docker compose version >/dev/null || { echo "ERRO: Docker Compose v2 nao encontrado."; exit 1; }

if [ ! -f .env ]; then
  echo "==> Criando .env a partir do exemplo"
  cp .env.example .env
  echo
  echo "  >>> Edite o .env e preencha GROQ_API_KEY e TELEGRAM_BOT_TOKEN."
  echo "  >>> Depois rode este script de novo."
  exit 0
fi

set -a; source .env; set +a

if [[ "${GROQ_API_KEY:-}" == *"cole_sua_chave"* || -z "${GROQ_API_KEY:-}" ]]; then
  echo "ERRO: preencha GROQ_API_KEY no .env"; exit 1
fi
if [[ "${TELEGRAM_BOT_TOKEN:-}" == *"cole_o_token"* || -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "ERRO: preencha TELEGRAM_BOT_TOKEN no .env"; exit 1
fi

echo "==> Subindo o Postgres"
docker compose up -d postgres

echo "==> Aguardando o banco ficar saudavel"
for i in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
    echo "    banco pronto."
    break
  fi
  sleep 2
  [ "$i" -eq 30 ] && { echo "ERRO: banco nao subiu a tempo."; exit 1; }
done

echo "==> Conferindo o schema"
TABELAS=$(docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';")
echo "    ${TABELAS} tabela(s) encontrada(s)."

if [ "${TABELAS}" -lt 5 ]; then
  echo "    aplicando schema manualmente..."
  docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < db/init.sql
  docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < analytics/views.sql
fi

echo "==> Construindo a imagem do bot"
docker compose build bot

echo "==> Ingerindo a base de conhecimento"
docker compose run --rm bot python -m src.ingest

echo
echo "Setup concluido."
echo
echo "Proximos passos:"
echo "  Testar sem Telegram : docker compose run --rm bot python -m src.cli"
echo "  Subir o bot         : docker compose up -d bot"
echo "  Ver logs            : docker compose logs -f bot"
