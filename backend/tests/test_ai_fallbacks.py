"""Tests for Block 7 — AI fallback events + dashboard endpoint.

Covers:
  1) Запись AIFallbackEvent при разных типах сбоев в get_ai_answer.
  2) Запись AIFallbackEvent при сбое classify_ticket в create_ticket.
  3) GET /stats/ai/fallbacks — агрегаты, авторизация, окно.

Тесты намеренно не дёргают реальную сеть: AI-сервис недостижим благодаря
autouse-фикстуре _isolate_ai_service в conftest.py + явные monkeypatch'и
там, где нужны конкретные типы исключений.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.ai_fallback_event import AIFallbackEvent


@pytest.fixture(autouse=True)
def _monkeypatch_sqlite_fallback(monkeypatch: pytest.MonkeyPatch):
    """Force SQLite FTS fallback for all tests in this file.

    Tests call find_knowledge_answer indirectly through generate_ai_message.
    Force SQLite dialect fallback to avoid search_vector dependency.
    """
    monkeypatch.setattr(
        "app.services.knowledge_base._session_dialect_name",
        lambda _db: "sqlite",
    )


async def _register(client: AsyncClient, suffix: str) -> tuple[int, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"fb{suffix}@example.com",
            "username": f"fb{suffix}",
            "password": "Secret123!",
        },
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    return me.json()["id"], token


async def _register_admin(client: AsyncClient, suffix: str) -> str:
    """Регистрирует пользователя через bootstrap-механизм и возвращает admin-token."""
    from app.config import get_settings

    settings = get_settings()
    settings.BOOTSTRAP_ADMIN_EMAIL = f"fbadmin{suffix}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"fbadmin{suffix}@example.com",
            "username": f"fbadmin{suffix}",
            "password": "Secret123!",
        },
    )
    settings.BOOTSTRAP_ADMIN_EMAIL = None
    assert response.status_code == 201
    return response.json()["access_token"]


# ── Запись событий ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ai_answer_records_connect_fallback(client: AsyncClient, db_session, monkeypatch):
    """Дохлый AI_SERVICE_URL → fallback с reason=connect и запись в БД.

    Это самый частый failure mode на демо: ai-service не поднят, /ai/answer
    превращается в connection refused. Нужен явный сигнал в дашборде.
    """
    user_id, _ = await _register(client, "ans1")

    # Создаём conversation вручную — упрощает интеграционный путь
    from app.models.conversation import Conversation
    from app.services.conversation_ai import generate_ai_message

    conversation = Conversation(user_id=user_id, status="ai_processing")
    db_session.add(conversation)
    await db_session.flush()
    from app.models.message import Message

    db_session.add(Message(conversation_id=conversation.id, role="user", content="hello?"))
    await db_session.flush()

    # autouse-фикстура _isolate_ai_service уже ставит дохлый URL → ConnectError
    await generate_ai_message(db_session, conversation.id)

    events = (
        (
            await db_session.execute(
                select(AIFallbackEvent).where(AIFallbackEvent.conversation_id == conversation.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].service == "answer"
    assert events[0].reason in {"connect", "http_5xx"}


@pytest.mark.asyncio
async def test_get_ai_answer_records_broken_json_fallback(
    client: AsyncClient, db_session, monkeypatch
):
    """Если AI-сервис ответил 200, но не-JSON — пишем reason=broken_json.

    Регрессия: эти случаи маскировались под обычный fallback и в дашборде
    выглядели как «AI просто недоступен». Теперь админ видит, что Mistral
    жив, но генерирует мусор — другой разговор и другой fix.
    """
    user_id, _ = await _register(client, "ans2")

    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.services import conversation_ai

    conversation = Conversation(user_id=user_id, status="ai_processing")
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(Message(conversation_id=conversation.id, role="user", content="anything"))
    await db_session.flush()

    # Подменяем httpx.AsyncClient на возвращающий не-JSON ответ
    class _OkResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _OkResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "AI_SERVICE_URL", "http://ai-service.test")

    await conversation_ai.generate_ai_message(db_session, conversation.id)

    events = (
        (
            await db_session.execute(
                select(AIFallbackEvent).where(AIFallbackEvent.conversation_id == conversation.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].reason == "broken_json"


@pytest.mark.asyncio
async def test_classify_ticket_records_connect_fallback(client: AsyncClient, db_session):
    """create_ticket с недоступным classify-сервисом → событие service=classify."""
    _, token = await _register(client, "cls1")
    response = await client.post(
        "/api/v1/tickets/",
        json={
            "title": "тест fallback",
            "body": "проверка записи fallback события",
            "user_priority": 3,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    ticket_id = response.json()["id"]

    events = (
        (
            await db_session.execute(
                select(AIFallbackEvent).where(AIFallbackEvent.ticket_id == ticket_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].service == "classify"
    assert events[0].reason in {"connect", "http_5xx"}
    assert events[0].conversation_id is None


# ── Endpoint /stats/ai/fallbacks ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallbacks_endpoint_aggregates_by_reason_and_service(client: AsyncClient, db_session):
    admin_token = await _register_admin(client, "agg")

    # Сидим разные события напрямую в БД — интеграционные пути уже проверили
    # запись событий выше; здесь нас интересует именно агрегатор.
    db_session.add_all(
        [
            AIFallbackEvent(service="answer", reason="connect"),
            AIFallbackEvent(service="answer", reason="connect"),
            AIFallbackEvent(service="answer", reason="timeout"),
            AIFallbackEvent(service="classify", reason="broken_json"),
        ]
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/stats/ai/fallbacks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["by_reason"] == {"connect": 2, "timeout": 1, "broken_json": 1}
    assert body["by_service"] == {"answer": 3, "classify": 1}


@pytest.mark.asyncio
async def test_fallbacks_endpoint_respects_since_window(client: AsyncClient, db_session):
    """События старше окна since не должны попадать в агрегат."""
    admin_token = await _register_admin(client, "win")

    old_event = AIFallbackEvent(service="answer", reason="connect")
    new_event = AIFallbackEvent(service="answer", reason="timeout")
    db_session.add_all([old_event, new_event])
    await db_session.flush()

    # Подменяем created_at у old_event на 25 часов назад (за пределами 24ч-окна).
    # Делаем UPDATE через SQL, чтобы обойти server_default=now().
    from sqlalchemy import update

    await db_session.execute(
        update(AIFallbackEvent)
        .where(AIFallbackEvent.id == old_event.id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=25))
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/stats/ai/fallbacks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = response.json()
    # Только новое событие в дефолтном 24ч-окне
    assert body["total"] == 1
    assert body["by_reason"] == {"timeout": 1}


@pytest.mark.asyncio
async def test_fallbacks_endpoint_forbidden_for_non_admin(client: AsyncClient):
    """Обычный user → 403; в выдаче чувствительные сигналы инфраструктуры."""
    _, token = await _register(client, "guest")
    response = await client.get(
        "/api/v1/stats/ai/fallbacks",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_fallbacks_endpoint_requires_auth(client: AsyncClient):
    response = await client.get("/api/v1/stats/ai/fallbacks")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_fallbacks_endpoint_clamps_since_to_max_window(client: AsyncClient, db_session):
    """since за пределами 30 дней клипуется на максимум, чтоб не сканировать всё."""
    admin_token = await _register_admin(client, "clamp")

    response = await client.get(
        "/api/v1/stats/ai/fallbacks",
        params={"since": "2000-01-01T00:00:00+00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    # since в ответе подрезан до now - 30 дней (с погрешностью на тестовое время)
    parsed = datetime.fromisoformat(body["since"])
    age = datetime.now(UTC) - parsed
    assert timedelta(days=29) < age < timedelta(days=31)
