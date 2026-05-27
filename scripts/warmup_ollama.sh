#!/usr/bin/env bash
# warmup_ollama.sh — скачивает модели и загружает их в оперативную память.
#
# Запускать ПОСЛЕ того, как стек поднят:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
#   bash scripts/warmup_ollama.sh
#
# Что делает:
#   1. Скачивает mistral (~4.1 ГБ) и nomic-embed-text (~274 МБ)
#   2. Отправляет тестовый запрос, чтобы модель загрузилась в RAM
#      (без этого первый реальный запрос пользователя будет ждать ~30-60 сек)

set -euo pipefail

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
OLLAMA="$COMPOSE exec ollama ollama"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${YELLOW}→${NC} $*"; }

echo ""
echo "=== Прогрев Ollama ==="
echo ""

# ── Скачиваем модели ────────────────────────────────────────────────────────
info "Скачиваем mistral (~4.1 ГБ, может занять 5–10 минут на 1 Гбит/с)..."
$OLLAMA pull mistral
ok "mistral готов."

info "Скачиваем nomic-embed-text (~274 МБ)..."
$OLLAMA pull nomic-embed-text
ok "nomic-embed-text готов."

# ── Прогрев: загружаем модель в RAM ─────────────────────────────────────────
info "Загружаем mistral в оперативную память (keep-alive = 1h)..."
$COMPOSE exec ollama sh -c \
  'curl -sf -X POST http://localhost:11434/api/generate \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"mistral\",\"prompt\":\"Привет\",\"stream\":false,\"keep_alive\":\"1h\"}" \
    | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get(\"response\",\"\")[:80])"'
ok "Mistral прогрет и держится в памяти."

info "Загружаем nomic-embed-text в оперативную память..."
$COMPOSE exec ollama sh -c \
  'curl -sf -X POST http://localhost:11434/api/embed \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"nomic-embed-text\",\"input\":[\"warmup\"],\"keep_alive\":\"30m\"}" > /dev/null'
ok "nomic-embed-text прогрет."

echo ""
ok "Все модели скачаны и загружены. Первый запрос пользователя будет быстрым."
echo ""
