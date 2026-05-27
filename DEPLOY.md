# Деплой на VPS (Hetzner + собственный домен)

Полный пошаговый гайд: поднять стек с HTTPS и LLM за один вечер.

---

## Предварительные требования

| Что нужно | Где взять |
|---|---|
| Аккаунт Hetzner | [hetzner.com/cloud](https://www.hetzner.com/cloud) — нужна карта |
| Домен | DuckDNS (бесплатно) или любой регистратор (~$10/год) |
| SSH-ключ | см. шаг 1 |
| Git на локальной машине | уже есть |

Оценка стоимости: **CPX41 (8 vCPU / 16 GB) = €0.05/ч**. Для демо на 2–3 часа — меньше €1.

---

## Шаг 1 — Подготовьте SSH-ключ

```bash
# Проверьте, есть ли ключ
ls ~/.ssh/id_ed25519.pub

# Если нет — сгенерируйте
ssh-keygen -t ed25519 -C "deploy"

# Скопируйте публичный ключ — он понадобится при создании сервера
cat ~/.ssh/id_ed25519.pub
```

---

## Шаг 2 — Создайте сервер на Hetzner

1. Зайдите в [console.hetzner.com](https://console.hetzner.com)
2. **New Server**:
   - Location: **Nuremberg** или **Helsinki** (ближе к вам)
   - Image: **Ubuntu 24.04**
   - Type: **CPX41** (8 vCPU / 16 GB / 200 GB NVMe) — ~€0.05/ч
   - SSH keys: добавьте ключ из шага 1
   - Name: `support-demo`
3. Нажмите **Create & Buy now**
4. Запомните **публичный IPv4-адрес** — он нужен для DNS

> ⚠️ Не забудьте удалить сервер после демо: **правой кнопкой → Delete**. Автоматически не гасится.

---

## Шаг 3 — Настройте домен

### Вариант A: DuckDNS (бесплатно, 5 минут)

1. Зайдите на [duckdns.org](https://www.duckdns.org) → войдите через GitHub
2. Создайте поддомен, например `support-demo` → получите `support-demo.duckdns.org`
3. В поле **current ip** введите IPv4-адрес вашего сервера → **update ip**

### Вариант B: Купленный домен

В панели управления регистратора создайте A-запись:

```
Тип:  A
Имя:  @ (или поддомен, например demo)
Значение: <IPv4 сервера>
TTL:  60
```

> DNS может распространяться 1–10 минут. Проверьте: `nslookup YOUR_DOMAIN`

---

## Шаг 4 — Подключитесь к серверу

```bash
ssh root@<IPv4 сервера>
```

При первом подключении подтвердите отпечаток (`yes`).

---

## Шаг 5 — Установите Docker

```bash
# Обновите пакеты
apt-get update && apt-get upgrade -y

# Установите Docker (официальный скрипт)
curl -fsSL https://get.docker.com | sh

# Проверьте
docker --version          # Docker 26+
docker compose version    # v2.24+
```

---

## Шаг 6 — Склонируйте репозиторий

```bash
git clone https://github.com/<ваш-юзернейм>/support-demo.git
cd support-demo
```

---

## Шаг 7 — Настройте окружение

```bash
# Создаст backend/.env из шаблона, сгенерирует JWT_SECRET_KEY,
# запросит POSTGRES_PASSWORD и BOOTSTRAP_ADMIN_EMAIL
bash setup.sh
```

Затем дополните `backend/.env` вручную:

```bash
# Откройте файл
nano backend/.env
```

Измените следующие строки:

```ini
# 1. Переключитесь в режим production
APP_ENV=production

# 2. Ваш домен (нужен для CORS — браузер проверяет)
CORS_ORIGINS=https://YOUR_DOMAIN

# 3. Сгенерируйте ключ для AI-сервиса (скопируйте и вставьте вывод)
#    python3 -c "import secrets; print(secrets.token_urlsafe(32))"
AI_SERVICE_API_KEY=<вставьте сгенерированный ключ>

# 4. Включите семантический поиск (работает через nomic-embed-text)
KNOWLEDGE_SEMANTIC_SEARCH_ENABLED=true
```

Сохраните: **Ctrl+O**, **Enter**, **Ctrl+X**.

---

## Шаг 8 — Укажите домен в Caddyfile

```bash
nano Caddyfile
```

Замените `YOUR_DOMAIN` на ваш домен:

```
support-demo.duckdns.org {
    reverse_proxy frontend:80
}
```

Сохраните.

---

## Шаг 9 — Запустите стек

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Первый запуск займёт **5–10 минут**: скачиваются образы, собирается frontend и ai-service.

Следите за логами:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f
```

Дождитесь строки `Application startup complete` в логах `app`.

---

## Шаг 10 — Скачайте и прогрейте LLM-модели

```bash
bash scripts/warmup_ollama.sh
```

Этот скрипт:
- скачает **mistral** (~4.1 ГБ) и **nomic-embed-text** (~274 МБ)
- загрузит модели в оперативную память

Скачивание займёт **5–15 минут** в зависимости от канала Hetzner. Последующие запуски контейнера модели не скачиваются заново — они хранятся в Docker volume `ollama_data`.

---

## Шаг 11 — Накатите миграции и заполните демо-данными

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# Миграции (идемпотентны, уже запускаются при старте app, но можно и вручную)
$COMPOSE exec app alembic upgrade head

# Демо-пользователи (admin / agent / user)
$COMPOSE exec app python scripts/seed_demo_agents.py

# База знаний (статьи KB)
$COMPOSE exec app python scripts/seed_knowledge_base.py
```

---

## Шаг 12 — Проверьте работу

```bash
# Все контейнеры должны быть healthy / running
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

Откройте в браузере: **https://YOUR_DOMAIN**

Caddy автоматически получит TLS-сертификат (~10–30 секунд при первом запросе).

### Демо-аккаунты

| Роль | Email | Пароль |
|---|---|---|
| Администратор | `admin@demo.local` | `DemoPass123!` |
| Агент поддержки | `agent@demo.local` | `DemoPass123!` |
| Пользователь | `user@demo.local` | `DemoPass123!` |

---

## Управление стеком

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# Перезапустить
$COMPOSE restart

# Обновить код
git pull
$COMPOSE up -d --build

# Логи конкретного сервиса
$COMPOSE logs -f app
$COMPOSE logs -f ai-service
$COMPOSE logs -f ollama
$COMPOSE logs -f caddy

# Статус всех сервисов
$COMPOSE ps

# Бэкап БД
bash scripts/backup_db.sh
```

---

## Смена модели (если Mistral медленный)

Для более быстрых ответов замените `mistral` на `qwen2.5:3b` (~2–4 Гб, время ответа ~3–6 с вместо 15–25 с):

```bash
# Скачайте меньшую модель
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec ollama \
    ollama pull qwen2.5:3b

# Обновите .env
nano backend/.env
# OLLAMA_MODEL=qwen2.5:3b  (добавьте или измените)

# Перезапустите ai-service
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart ai-service
```

---

## Завершение демо — удалите сервер

1. Зайдите в [console.hetzner.com](https://console.hetzner.com)
2. Выберите сервер `support-demo`
3. **Actions → Delete**

Почасовая оплата прекращается в момент удаления.

---

## Диагностика

### Caddy не получает сертификат

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs caddy
```

Причины:
- A-запись домена ещё не распространилась — подождите 5–10 минут
- Порт 443 закрыт в firewall — проверьте `ufw status` и Hetzner Firewall Rules

### ai-service падает при старте (APP_ENV=production)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs ai-service
```

Убедитесь, что `AI_SERVICE_API_KEY` задан в `backend/.env` и не пустой.

### Ollama не отвечает

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec ollama ollama list
```

Если моделей нет — запустите `bash scripts/warmup_ollama.sh`.

### Проверка API напрямую

```bash
# Healthcheck backend
curl https://YOUR_DOMAIN/api/v1/healthcheck

# Healthcheck ai-service (только изнутри сети)
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec app curl -s http://ai-service:8001/healthcheck
```
