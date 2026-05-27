# Support Demo
https://bits-topic-sic-served.trycloudflare.com 
# Точка поддержки

AI-сервис для автоматизации внутренних обращений сотрудников в службу поддержки.

## Возможности

- чат сотрудника с AI-ассистентом;
- поиск по базе знаний;
- автоматическая классификация обращений;
- создание тикета при неуверенности AI;
- маршрутизация по отделам;
- локальный запуск LLM через Ollama;
- self-hosted архитектура.

## Архитектура

Frontend: React + Vite + Mantine  
Backend: FastAPI + PostgreSQL + SQLAlchemy + Alembic  
AI-service: FastAPI + Ollama + Mistral + nomic-embed-text  
RAG: база знаний + full-text search + semantic search через embeddings

### База знаний (RAG)

Система использует RAG-поиск по `knowledge_articles`: гибрид FTS (PostgreSQL
`websearch_to_tsquery`) + семантический поиск (pgvector с эмбеддингами от
Ollama `nomic-embed-text`). Это то, что AI-ассистент цитирует пользователю,
когда отвечает в чате.

**MVP / демо:** при первом запуске сидируется синтетическая база знаний
из `backend/scripts/seed_data/articles/*.json` — типовые внутренние
инструкции по 7 отделам:

| Отдел | Покрытие |
|-------|----------|
| `IT` | VPN, сброс пароля, принтер, доступы, Wi-Fi, установка ПО |
| `HR` | справки, отпуск, больничный, изменение персональных данных |
| `finance` | оплата счетов, авансовые отчёты, возмещение, сверка |
| `procurement` | закупка у нового поставщика, канцелярия, согласование договора |
| `security` | фишинг, компрометация, потеря устройства, подозрительная активность |
| `facilities` | пропуска, климат, мебель, бронирование переговорных |
| `documents` | заверение копий, архив, ЭЦП, согласование документов |

Эта база — **имитация** того, что обычно лежит в Confluence/SharePoint
у заказчика. Она нужна, чтобы продемонстрировать работу RAG из коробки,
без подключения к корпоративным источникам.

**При внедрении к заказчику** синтетическая база заменяется на статьи
из его источников:

```powershell
# Confluence-export или git-репо с MD-файлами + YAML frontmatter:
python -m scripts.import_knowledge_from_markdown C:\path\to\confluence-md\

# CSV-выгрузка из ServiceNow / SharePoint List / Excel:
python -m scripts.import_knowledge_from_csv kb_export.csv

# CSV с нестандартными именами колонок (ServiceNow часто Subject/Group):
python -m scripts.import_knowledge_from_csv kb.csv `
  --map "title=Subject,department=Group,body=Description"

# Только проверить парсинг, не записывая в БД:
python -m scripts.import_knowledge_from_csv kb.csv --dry-run
```

Все три пути (seed JSON, MD, CSV) идут через единую точку
`app/services/knowledge_ingestion.py` — валидация, upsert по `title`,
sync-индексация и enqueue embedding-job. Импорты идемпотентны: повторный
запуск обновит существующие статьи, дубликатов не появится.

После импорта `knowledge-embedding-worker` подхватит embedding-jobs
(см. ниже) и заиндексирует чанки в pgvector — обычно занимает минуты
на сотнях статей.

### Фоновые воркеры

Backend поднимается не одним процессом, а четырьмя — API + три воркера. Это
видно в `backend/docker-compose.dev.yml` как отдельные сервисы:

| Сервис | Что делает | Что сломается, если не запустить |
|--------|------------|----------------------------------|
| `app` | FastAPI — принимает HTTP-запросы | Без него ничего не работает |
| `ai-worker` | Достаёт `ai_jobs` из очереди, генерирует AI-ответы в чате | Чат принимает сообщения, но AI-ответов не будет — `conversation.status` навсегда зависнет в `ai_processing` |
| `knowledge-embedding-worker` | Считает embeddings для чанков базы знаний (Ollama → pgvector) | Семантический поиск не работает — RAG деградирует до FTS-only |
| `sla-worker` | Эскалирует тикеты, у которых истёк SLA, и крутит retention-задачи | SLA не эскалируется автоматически; старые `audit_logs` / `ai_jobs` копятся вечно |

В Docker'е (`start.ps1`) все четыре стартуют автоматически — править ничего
не нужно. Если backend поднимается без Docker (чистый `uvicorn` для
разработки) — воркеры надо запустить руками в отдельных терминалах:

```powershell
cd backend
$env:PYTHONPATH = "."
# Терминал 1 — API
.\.venv\Scripts\python -m uvicorn app.main:app --reload
# Терминал 2 — AI-ответы в чате
.\.venv\Scripts\python -m app.workers.ai_worker
# Терминал 3 — индексация knowledge_chunks
.\.venv\Scripts\python -m app.workers.knowledge_embedding_worker
# Терминал 4 — SLA-эскалация и retention
.\.venv\Scripts\python -m app.workers.sla_worker
```

Каждый воркер — отдельный python-процесс с polling-циклом
(см. `backend/app/workers/*.py`). Останавливаются через `Ctrl+C` или SIGTERM
(в Docker — `docker compose stop ai-worker`).

## Предусловия

Перед первым запуском должны быть установлены:

- **Docker Desktop** — для backend + Postgres ([download](https://www.docker.com/products/docker-desktop/))
- **Ollama** — локальный LLM-runtime ([download](https://ollama.com/download)).
  После установки выполните один раз:
  ```powershell
  ollama pull mistral
  ollama pull nomic-embed-text
  ```
- **Python 3.12** — для AI-сервиса ([download](https://www.python.org/downloads/))
- **Node.js 18+** — для фронта ([download](https://nodejs.org/))

`start.ps1` сам проверит, что Docker / Python / Node на месте, и подскажет
ссылку на установку, если чего-то не хватает.

## Start

Выполните одну команду из корневой директории репозитория:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File .\stop.ps1
```


Что происходит при первом запуске:

- Создаётся `ai/ai-service/.venv` с зависимостями (~1-2 мин).
- Создаётся `backend/.env` из `.env.example` (для prod-развёртывания значения
  в нём надо подправить — см. `backend/docs/deployment.md`).
- `npm install` поднимает `node_modules` фронта.
- Поднимается Ollama (если ещё не запущена).
- Поднимается AI-сервис на `http://localhost:8001`.
- Поднимается backend + Postgres в Docker (`docker-compose.dev.yml`).
- Поднимается dev-сервер фронта на `http://localhost:5173`.

Backend и frontend открываются в отдельных окнах PowerShell — в них видны
логи, и закрывать их можно по отдельности.

## End

Из `C:\Code\support-demo`:

```powershell
cd C:\Code\support-demo
```

Остановить backend + Postgres:

```powershell
cd backend
docker compose -f docker-compose.dev.yml down
```

Остановить frontend и AI service, если они запущены из `start.ps1`:

```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 5173).OwningProcess -Force
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8001).OwningProcess -Force
```

Остановить Ollama:

```powershell
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
```

Проверить, что порты свободны:

```powershell
netstat -ano | findstr ":8000 :8001 :5173 :5432"
```

Удалить ещё и данные Postgres, вместо обычного `down`:

```powershell
docker compose -f docker-compose.dev.yml down -v
```

`-v` удалит volume с базой. Для обычного завершения — без `-v`.
