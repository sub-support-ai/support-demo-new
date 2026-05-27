# CONTRIBUTING — RestAPI (Точка поддержки)

Onboarding для нового разработчика этого репозитория. Прочитай в начале
работы — здесь зафиксированы правила, которых придерживается команда,
и контекст, без которого изменения легко увести не туда.

---

## 1. Контекст проекта

### Что мы делаем
**"Точка поддержки"** — корпоративная система техподдержки. Пользователь
пишет в чат, AI-сервис на Mistral через Ollama отвечает. Если AI не
уверен или не справляется — система в один клик создаёт тикет и роутит
его агенту в нужный отдел (IT / HR / finance).

### Архитектура (backend + AI-сервис)
1. **Backend** (`backend/`) — FastAPI + Postgres. Отвечает за: пользователей,
   авторизацию, тикеты, диалоги, агентов, роутинг, аудит. Зовёт AI-сервис по HTTP.
2. **AI-сервис** (`ai/ai-service/`) — FastAPI + Ollama (Mistral для ответов,
   nomic-embed-text для эмбеддингов). Эндпоинты `POST /ai/classify` и
   `POST /ai/answer`. Разворачивается отдельным процессом, но лежит в этом же
   репозитории. Контракт — `docs/ai-lead-contract.md`.

> **Историческая справка.** Раньше AI-сервис («AI-Lead») вёлся отдельной
> командой в отдельном репозитории на контракте `message: str`. Сейчас он
> перенесён в этот репозиторий (`ai/ai-service/`) и работает на multi-turn
> контракте `messages: list`. Упоминания внешнего репозитория, `D:\Code\AI-Lead`
> и ожидаемого 422 в старых записях CHANGELOG — исторический контекст, а не
> текущее состояние.

### Что это значит на практике
- ✅ AI-сервис — обособленный процесс с собственным контрактом. Меняешь
  контракт — обновляй `docs/ai-lead-contract.md` и синхронно правь обе стороны:
  backend и `ai/ai-service/`.
- ✅ **Защищай backend от сбоев AI-сервиса:** все запросы — через try/except
  с fallback (см. `get_ai_answer` в `app/services/conversation_ai.py`), все
  ответы — через `data.setdefault(...)` с дефолтами на отсутствующие поля.

### Ключевые принципы из плана проекта
- **Красная зона:** confidence < 0.6 → НЕ показываем ответ модели,
  форсим эскалацию на агента. (`app/routers/conversations.py`, константа
  `RED_ZONE_THRESHOLD`.)
- **1-click автотикет:** из истории чата собирается title/body/category/
  priority/steps_tried, пользователь подтверждает одним кликом.
  (`POST /conversations/{id}/escalate`.)
- **Цитирование источников (RAG):** ответ модели сопровождается
  `sources: list[{title, url}]`, UI показывает их рядом. (Колонка
  `Message.sources`, JSON.)
- **Multi-turn контракт `/ai/answer`:** `messages: list[{role, content}]`,
  не одна строка. role="assistant" для AI, role="user" для пользователя.
  Системные сообщения от клиента отбрасываются на стороне AI-сервиса —
  защита от prompt injection.
- **AILog для дообучения:** каждое решение модели пишется с реальной
  `model_version` (никогда не литерал `"unknown"` — используй
  `settings.AI_MODEL_VERSION_FALLBACK`).

---

## 2. Карта репозитория

```
app/
├── main.py                  # FastAPI app, роутеры, CORS, healthcheck
├── config.py                # Settings (env), get_settings(), JWT_SECRET валидация
├── database.py              # async SQLAlchemy engine, get_db, Base
├── dependencies.py          # get_current_user, require_role
├── rate_limit.py            # in-memory лимитер на /auth/*
├── logging_config.py        # setup_logging
├── sentry_config.py         # setup_sentry (опционально)
│
├── models/                  # SQLAlchemy ORM
│   ├── user.py              # User (role: user|agent|admin)
│   ├── agent.py             # Agent (department, ai_routing_score, active_ticket_count)
│   ├── ticket.py            # Ticket (conversation_id, steps_tried, ai_*)
│   ├── conversation.py      # Conversation (status: active|resolved|escalated|...)
│   ├── message.py           # Message (sources, ai_confidence, requires_escalation)
│   ├── response.py          # Response (ai/agent draft)
│   ├── ai_log.py            # AILog (model_version, outcome, agent_*)
│   └── audit_log.py         # AuditLog (без FK на users — для удалённых)
│
├── routers/
│   ├── auth.py              # POST /auth/register, /auth/login, GET /auth/me
│   ├── users.py             # GET /users/, /users/{id}, PATCH role (admin)
│   ├── tickets.py           # CRUD + PATCH /resolve
│   ├── conversations.py     # POST/GET /messages, POST /escalate (1-click)
│   ├── stats.py             # GET /stats (метрики для дашборда)
│   └── audit.py             # GET /audit (admin only)
│
├── services/
│   ├── ai_classifier.py     # httpx клиент к /ai/classify
│   ├── routing.py           # assign_agent / unassign_agent
│   └── audit.py             # log_event
│
└── schemas/
    └── ticket.py            # Pydantic: TicketCreate, TicketRead, ...

alembic/versions/            # миграции (по одной на feature)
tests/                       # pytest, asyncio_mode=auto, SQLite по умолчанию
docs/                        # ТЗ, спеки контрактов, диаграммы
CHANGELOG.md                 # Keep-a-Changelog, секция Unreleased
```

---

## 3. Контракт с AI-сервисом

**Источник истины контракта:** [`docs/ai-lead-contract.md`](docs/ai-lead-contract.md).
Это формальное описание того, что backend ожидает от AI-сервиса. Если что-то
в контракте надо поменять — начинай с этого документа, затем синхронно правь
обе стороны (backend и `ai/ai-service/`).

**Проверить, что AI-сервис сейчас реально отдаёт:**
```bash
cat ai/ai-service/main.py        # эндпоинты /ai/classify и /ai/answer
cat ai/ai-service/answerer.py    # логика генерации ответа
```

### Целевой контракт (см. `docs/ai-lead-contract.md`)

#### POST /ai/answer
**Запрос:**
```json
{
  "conversation_id": 42,
  "messages": [
    {"role": "user", "content": "Не могу войти в SAP"},
    {"role": "assistant", "content": "Какую систему?"},
    {"role": "user", "content": "SAP, версия 7"}
  ]
}
```
**Ответ:**
```json
{
  "answer": "Зайдите на портал...",
  "confidence": 0.85,
  "escalate": false,
  "sources": [{"title": "Регламент VPN", "url": "https://wiki/vpn"}],
  "model_version": "mistral-7b-instruct-q4_K_M-2026-04"
}
```

#### POST /ai/classify
**Ответ:**
```json
{
  "category": "it_access",
  "department": "IT",
  "priority": "высокий",
  "confidence": 0.9,
  "draft_response": "...",
  "model_version": "mistral-7b-instruct-q4_K_M-2026-04"
}
```

**Категории (12):** it_hardware, it_software, it_access, it_network,
hr_payroll, hr_leave, hr_policy, hr_onboarding, finance_invoice,
finance_expense, finance_report, other.
**Departments (4):** IT, HR, finance, other (other → fallback на IT в Ticket).

### Текущее состояние AI-сервиса

AI-сервис (`ai/ai-service/`) работает на актуальном multi-turn контракте:
принимает `messages: list[{role, content}]`, возвращает
`{answer, confidence, escalate, sources, model_version}`. Если сервис
недоступен или вернёт некорректный ответ — срабатывает fallback в
`get_ai_answer` (confidence=0.0, escalate=True → красная зона), пользователь
сразу увидит кнопку эскалации, "белого экрана" не будет.

---

## 4. Правила работы

### A. Документируй всё в CHANGELOG
Каждое изменение → запись в `CHANGELOG.md` в секцию `## [Unreleased]`,
по разделам: Added / Changed / Deprecated / Removed / Fixed / Security.
Формат — Keep-a-Changelog. Описывай **зачем** меняешь, не только что.

### B. Никаких больших коммитов
Один коммит = одна логическая единица. Conventional Commits на русском:
```
feat(routers): добавить POST /escalate для 1-click тикета
fix(routing): вторичная сортировка по id для детерминизма
docs: обновить CHANGELOG для R1-R4
test: тесты на красную зону confidence < 0.6
chore: миграция b2c4e6f8a0d2 для Message.ai_metadata
```

### C. Миграции
- Каждая schema-change → новая миграция `alembic/versions/<hash>_<name>.py`.
- ID берём из `python -c "import secrets; print(secrets.token_hex(6))"`.
- `down_revision` = ID последней существующей миграции (`alembic history`).
- Используй `op.batch_alter_table` для ALTER на колонках — нужно для
  совместимости с SQLite (тесты).
- Всегда пиши `downgrade()` — даже если кажется, что не понадобится.

### D. Тесты обязательны
Любое изменение в роутере / сервисе / модели → новый тест в `tests/`.
Запуск:
```bash
"/c/Users/Daya/AppData/Local/Programs/Python/Python314/python.exe" -m pytest -q
```
(Системный `python` указывает на Microsoft Store — не сработает, нужен
полный путь к `Programs/Python/Python314/python.exe`.)

Тесты используют SQLite + `httpx.AsyncClient(transport=ASGITransport(app=app))`.
AI-сервис в тестах **недоступен** — `_get_ai_answer` уходит в fallback
(`confidence=0.0, escalate=True`). Это намеренно: проверяем самый частый
failure mode (внешний сервис лежит / тормозит).

### E. Безопасность — read-only защиты, не трогай
1. JWT_SECRET_KEY fail-fast в production (`app/config.py`).
2. Bcrypt 72-byte truncation guard в auth.
3. Rate limit на `/auth/*` (см. `app/rate_limit.py`).
4. 404 (не 403) при чужом ресурсе — не палим существование ID.
5. `user_id` в тикетах ВСЕГДА из JWT, никогда из тела запроса.
6. AuditLog без FK на `users` — действия удалённого пользователя остаются.

Если нужно ослабить любое из этого — сначала спроси.

### F. Что не делать
- ❌ Литерал `"unknown"` в `model_version` — отравляет датасет.
- ❌ `find_packages()` без `package_dir` для flat-layout пакетов.
- ❌ `git add -A` или `git add .` — могут протащить `.env`, `test.db`.
- ❌ Конкретные литералы в `confidence < X` без вынесения в константу.
- ❌ Скрытые prints / отладочные логи в коде → используй `logger`.
- ❌ Пустые `except:` — лови конкретные исключения от httpx.

### G. Когда что-то непонятно
1. Прочитай тесты — они показывают expected behavior.
2. Прочитай docstring модели — там описан жизненный цикл и зачем поле.
3. Прочитай актуальный AI-Lead контракт:
   `git -C D:/Code/AI-Lead show origin/ml1/AI-Lead:ai-service/main.py`.
4. Если всё ещё неясно — **спроси, не угадывай**.

---

## 5. Текущее состояние

### Сделано
- ✅ Базовая модель User/Agent/Ticket/Conversation/Message/Response/AILog/AuditLog.
- ✅ JWT-аутентификация + role-based (user/agent/admin).
- ✅ Routing: `assign_agent` (свободный vs старший по confidence ≥/<0.8).
- ✅ Rate limit, audit log, fail-fast secrets.
- ✅ **R1**: шлём `messages: list` в `/ai/answer` — целевой контракт
  (см. `docs/ai-lead-contract.md`). До обновления AI-сервиса их сервер
  вернёт 422 → наш fallback переведёт пользователя в красную зону.
- ✅ **R2**: `sources` / `model_version` парсятся через setdefault —
  работает с любой версией AI-сервиса.
- ✅ **R3**: красная зона confidence < 0.6 → `requires_escalation`.
- ✅ **R4**: POST /conversations/{id}/escalate (1-click autofill).
- ✅ **O5**: убран литерал `"unknown"` в `model_version`.
- ✅ Создан `docs/ai-lead-contract.md` — формальное ТЗ для команды AI-Lead.

### НЕ сделано (приоритет по убыванию)
- ⬜ **O6**: agent роль не видит назначенные тикеты в `list_tickets`
  (`app/routers/tickets.py:190` — для не-admin фильтр только по `user_id`).
- ⬜ **O7**: rate limit на `POST /tickets`, `POST /messages`.
- ⬜ **Y9**: `AILog.outcome` ставится только в `escalate_conversation` и
  в `/resolve` неявно. Нужно расставить во всех путях:
  resolved_by_ai / escalated_ai_ticket / escalated_user_ticket / declined.
- ⬜ **Y10**: переход `Conversation.status="resolved"` при положительном
  фидбэке пользователя ("AI помог" кнопка). Сейчас "active" → "escalated"
  работает, "active" → "resolved" нет.
- ⬜ Frontend integration tests (E2E через httpx, проверка сценариев).

### Известные технические долги
- В `tests/conftest.py` SQLite не строго совместим с Postgres JSON-типом.
  Если тесты пройдут на SQLite, но сломаются на Postgres — проверить
  `Message.sources` (используется `sa.JSON`, должно работать).
- `_extract_steps_tried` — наивная эвристика по ключевым словам. Iteration 2:
  переписать через LLM-вызов с структурированным промптом.

---

## 6. Чеклист перед PR

```
□ CHANGELOG.md обновлён (секция [Unreleased])
□ Новые тесты добавлены и зелёные
□ Все существующие тесты зелёные
□ Миграция написана (если schema change) и downgrade проверен
□ Никаких .env, .db, __pycache__ в коммите
□ Conventional Commits на русском
□ docstring у новых публичных функций — зачем, не только как
□ Comments на нетривиальные решения (почему именно так)
```

---

## 7. Полезные команды

```bash
# Тесты
"/c/Users/Daya/AppData/Local/Programs/Python/Python314/python.exe" -m pytest -q

# Один файл
... -m pytest tests/test_conversations.py -v

# Один тест
... -m pytest tests/test_conversations.py::test_escalate_creates_prefilled_ticket -v

# Миграции
py -m alembic upgrade head
py -m alembic current
py -m alembic history
py -m alembic revision -m "описание изменения"

# Локальный запуск
py -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Docker
docker compose up --build
```

---

## 8. Связь с AI-сервисом

AI-сервис (`ai/ai-service/`) — обособленный процесс со своим контрактом
(`/ai/answer`, `/ai/classify`). Меняешь контракт — правь синхронно обе стороны
и обновляй `docs/ai-lead-contract.md`.

**Держим backend forward-compatible** (полезно при разной скорости релизов
двух сторон):
- В **запросах** — лишние поля безопасны: Pydantic в FastAPI игнорирует
  extra-поля по умолчанию.
- В **ответах** — читаем все поля через `data.setdefault(...)` / `data.get(...)`,
  чтобы новые поля не падали с KeyError и аккуратно дефолтились.

**Защита от поломки AI-сервиса.** Любая ошибка из `/ai/answer` (timeout,
HTTP error, невалидный JSON) → fallback в `get_ai_answer`:
`{confidence: 0.0, escalate: True, ...}` → `requires_escalation: True`
→ клиент сразу предлагает пользователю эскалацию. Пользователь никогда
не увидит "AI лежит" — система деградирует к "сразу к агенту".
