"""Тесты интеграции backend → AI-сервис.

Что проверяем:
  - classify_ticket: happy-path (новый контракт с department, model_version),
    отсутствующие поля, 422/500/timeout/invalid-JSON → safe fallback.
  - get_ai_answer: happy-path (messages: list, sources, model_version),
    частичный ответ → setdefault-дефолты, 422/timeout/connect → fallback.
  - Регрессионный тест: backend шлёт `messages: list`, а не старый `message: str`.

Все тесты работают без реального AI-сервиса: httpx.AsyncClient мокируется
на уровне пакета, а local fixture ставит моковый HTTP URL, чтобы запрос дошёл до mock.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.ai_classifier import classify_ticket
from app.services.conversation_ai import get_ai_answer


@pytest.fixture(autouse=True)
def _use_mocked_http_ai_url(monkeypatch):
    from app.config import get_settings
    from app.services import ai_classifier as ai_classifier_module

    monkeypatch.setattr(get_settings(), "AI_SERVICE_URL", "http://ai-service.test")
    monkeypatch.setattr(ai_classifier_module, "AI_SERVICE_URL", "http://ai-service.test")


# ── Helpers ───────────────────────────────────────────────────────────────────


class _MockResponse:
    """Заглушка httpx-ответа: raise_for_status + json()."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def _mock_client(response: Any) -> AsyncMock:
    """Возвращает мок AsyncClient, у которого post() возвращает response."""
    mc = AsyncMock()
    mc.post = AsyncMock(return_value=response)
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=None)
    return mc


def _timeout_client() -> AsyncMock:
    mc = AsyncMock()
    mc.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=None)
    return mc


def _connect_error_client() -> AsyncMock:
    mc = AsyncMock()
    mc.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=None)
    return mc


# ── classify_ticket ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_new_contract_full_response():
    """Полный ответ нового контракта: department, model_version парсятся как есть."""
    payload = {
        "category": "it_access",
        "department": "IT",
        "priority": "высокий",
        "confidence": 0.92,
        "draft_response": "Проверьте логин.",
        "model_version": "mistral-7b-instruct-q4_K_M-2026-04",
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(_MockResponse(payload))):
        result = await classify_ticket(1, "не могу войти", "пароль не работает")

    assert result["category"] == "it_access"
    assert result["department"] == "IT"
    assert result["priority"] == "высокий"
    assert result["confidence"] == 0.92
    assert result["model_version"] == "mistral-7b-instruct-q4_K_M-2026-04"


@pytest.mark.asyncio
async def test_classify_missing_department_uses_it_fallback():
    """Если AI не вернул department — используется fallback 'IT'.

    Логика намеренная: тикет не должен зависнуть без исполнителя. 'IT' —
    безопасный default, который можно вручную переназначить. Маппинг
    category → department — ответственность AI-сервиса (classifier.py);
    когда он возвращает поле department, backend использует его (см. тест выше).
    """
    payload = {
        "category": "hr_leave",
        "priority": "низкий",
        "confidence": 0.80,
        "draft_response": "Оформите заявление.",
        "model_version": "test-v1",
        # department отсутствует → backend fallback = "IT"
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(_MockResponse(payload))):
        result = await classify_ticket(2, "отпуск", "хочу в отпуск")

    assert result["department"] == "IT"  # fallback, не из category


@pytest.mark.asyncio
async def test_classify_missing_model_version_uses_env_fallback():
    """Отсутствующий model_version → AI_MODEL_VERSION_FALLBACK из .env."""
    payload = {
        "category": "it_software",
        "department": "IT",
        "priority": "средний",
        "confidence": 0.75,
        "draft_response": "Переустановите.",
        # model_version отсутствует
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(_MockResponse(payload))):
        result = await classify_ticket(3, "вылетает", "1С вылетает")

    from app.config import get_settings

    assert result["model_version"] == get_settings().AI_MODEL_VERSION_FALLBACK


@pytest.mark.asyncio
async def test_classify_422_returns_safe_fallback():
    """AI вернул 422 (старый контракт не принял запрос) → safe fallback.

    Это критичный регрессионный тест: в период до обновления AI-Lead каждый
    POST /ai/classify возвращал 422. Пользователь не должен видеть 500 —
    тикет обязан уйти к агенту через fallback.
    """
    with patch("httpx.AsyncClient", return_value=_mock_client(_MockResponse({}, 422))):
        result = await classify_ticket(4, "тест", "тест")

    assert result["confidence"] == 0.0
    assert result["category"] == "other"
    # department из fallback — IT (чтобы тикет не завис без исполнителя)
    assert result["department"] == "IT"


@pytest.mark.asyncio
async def test_classify_500_returns_safe_fallback():
    """Internal Server Error от AI → fallback, не 500 для пользователя."""
    with patch("httpx.AsyncClient", return_value=_mock_client(_MockResponse({}, 500))):
        result = await classify_ticket(5, "тест", "тест")

    assert result["confidence"] == 0.0


@pytest.mark.asyncio
async def test_classify_timeout_returns_safe_fallback():
    """Timeout AI-сервиса → fallback (Mistral на CPU может отвечать 30–120 сек)."""
    with patch("httpx.AsyncClient", return_value=_timeout_client()):
        result = await classify_ticket(6, "тест", "тест")

    assert result["confidence"] == 0.0


@pytest.mark.asyncio
async def test_classify_invalid_json_returns_safe_fallback():
    """AI вернул невалидный JSON (модель «заболталась») → fallback."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.side_effect = json.JSONDecodeError("bad json", "", 0)

    with patch("httpx.AsyncClient", return_value=_mock_client(mock_resp)):
        result = await classify_ticket(7, "тест", "тест")

    assert result["confidence"] == 0.0


# ── get_ai_answer ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ai_answer_happy_path():
    """Полный ответ нового контракта: sources, model_version — всё доходит."""
    payload = {
        "answer": "Перезагрузите маршрутизатор.",
        "confidence": 0.88,
        "escalate": False,
        "sources": [{"title": "Инструкция VPN", "url": None}],
        "model_version": "mistral-7b-instruct-q4_K_M-2026-04",
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(_MockResponse(payload))):
        result = await get_ai_answer(1, [{"role": "user", "content": "vpn не работает"}])

    assert result["answer"] == "Перезагрузите маршрутизатор."
    assert result["confidence"] == 0.88
    assert result["escalate"] is False
    assert result["sources"] == [{"title": "Инструкция VPN", "url": None}]
    assert result["model_version"] == "mistral-7b-instruct-q4_K_M-2026-04"


@pytest.mark.asyncio
async def test_get_ai_answer_partial_response_uses_setdefaults():
    """Частичный ответ (только answer) → поля заполняются через setdefault.

    Контракт: backend не падает при отсутствии любого поля ответа.
    """
    with patch(
        "httpx.AsyncClient",
        return_value=_mock_client(_MockResponse({"answer": "Попробуйте перезагрузить."})),
    ):
        result = await get_ai_answer(2, [{"role": "user", "content": "что-то сломалось"}])

    assert result["answer"] == "Попробуйте перезагрузить."
    assert result["confidence"] == 0.5  # setdefault
    assert result["escalate"] is False  # setdefault
    assert result["sources"] == []  # setdefault
    from app.config import get_settings

    assert result["model_version"] == get_settings().AI_MODEL_VERSION_FALLBACK


@pytest.mark.asyncio
async def test_get_ai_answer_422_returns_fallback():
    """422 от AI (старый контракт не принял messages: list) → fallback + escalate.

    Пользователь видит «AI временно недоступен» и кнопку эскалации,
    а не белый экран или 500.
    """
    with patch("httpx.AsyncClient", return_value=_mock_client(_MockResponse({}, 422))):
        result = await get_ai_answer(3, [{"role": "user", "content": "вопрос"}])

    assert result["confidence"] == 0.0
    assert result["escalate"] is True
    assert result["answer"]  # не пустая строка — есть сообщение для пользователя


@pytest.mark.asyncio
async def test_get_ai_answer_timeout_returns_fallback():
    """Timeout Mistral (CPU-инференс) → fallback."""
    with patch("httpx.AsyncClient", return_value=_timeout_client()):
        result = await get_ai_answer(4, [{"role": "user", "content": "вопрос"}])

    assert result["confidence"] == 0.0
    assert result["escalate"] is True


@pytest.mark.asyncio
async def test_get_ai_answer_connect_error_returns_fallback():
    """ConnectError (AI-сервис не запущен) → fallback."""
    with patch("httpx.AsyncClient", return_value=_connect_error_client()):
        result = await get_ai_answer(5, [{"role": "user", "content": "вопрос"}])

    assert result["confidence"] == 0.0
    assert result["escalate"] is True


# ── Регрессионный тест на формат контракта ───────────────────────────────────


@pytest.mark.asyncio
async def test_get_ai_answer_sends_messages_list_not_message_str():
    """Backend шлёт messages: list[{role, content}], не старый message: str.

    Если кто-то случайно вернёт старый формат — этот тест упадёт
    раньше, чем изменение уйдёт на prod.
    """
    captured: dict[str, Any] = {}

    async def capture_and_reply(url: str, **kwargs: Any) -> _MockResponse:
        captured.update(kwargs.get("json", {}))
        return _MockResponse(
            {
                "answer": "ok",
                "confidence": 0.9,
                "escalate": False,
                "sources": [],
                "model_version": "test",
            }
        )

    mc = AsyncMock()
    mc.post = capture_and_reply
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=None)

    history = [
        {"role": "user", "content": "первый вопрос"},
        {"role": "assistant", "content": "первый ответ"},
        {"role": "user", "content": "второй вопрос"},
    ]

    with patch("httpx.AsyncClient", return_value=mc):
        await get_ai_answer(6, history)

    # Новый контракт: messages: list
    assert "messages" in captured, (
        "Backend должен слать messages: list — новый контракт. "
        "Старый message: str не поддерживает multi-turn и был убран."
    )
    assert isinstance(captured["messages"], list)
    # Старый контракт не должен присылаться
    assert "message" not in captured, "Старый формат message: str не должен присылаться"
    # conversation_id обязателен
    assert captured["conversation_id"] == 6
    # Вся история передаётся
    assert len(captured["messages"]) == len(history)
    # Роли корректны
    roles = {m["role"] for m in captured["messages"]}
    assert roles <= {"user", "assistant"}, "Роль system не должна попасть в payload"
