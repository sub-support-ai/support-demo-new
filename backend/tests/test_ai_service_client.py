import pytest

from app.config import get_settings
from app.services.ai_service_client import ai_service_headers


def test_ai_service_headers_empty_without_key(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)

    assert ai_service_headers() == {}

    get_settings.cache_clear()


def test_ai_service_headers_include_configured_key(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AI_SERVICE_API_KEY", "local-secret")

    assert ai_service_headers() == {"X-AI-Service-Key": "local-secret"}

    get_settings.cache_clear()


# ── Блок 3: AI latency capture ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ai_answer_includes_latency_in_fallback_payload(monkeypatch):
    """get_ai_answer кладёт _latency_ms в payload даже когда сервис недоступен.

    Это важно для дашборда: даже неудачные попытки должны попадать в среднее
    время ответа, иначе медленные сбои AI-сервиса прячутся за статистикой
    по успешным вызовам.

    Тест живёт здесь, а не в test_conversations.py: там autouse-фикстура
    _stub_ai_services перекрывает get_ai_answer целиком, а нам нужно
    вызвать реальную функцию.
    """
    from app.services.conversation_ai import get_ai_answer
    from app.services.knowledge_base import LATENCY_PAYLOAD_KEY

    # Дохлый URL — функция вернёт fallback без реального network I/O.
    monkeypatch.setattr(get_settings(), "AI_SERVICE_URL", "test://ai-service")

    payload = await get_ai_answer(
        conversation_id=1,
        messages=[{"role": "user", "content": "hello"}],
    )

    assert LATENCY_PAYLOAD_KEY in payload
    assert isinstance(payload[LATENCY_PAYLOAD_KEY], int)
    assert payload[LATENCY_PAYLOAD_KEY] >= 0
    # И что это действительно fallback, а не случайно дозвонились
    assert payload["confidence"] == 0.0
    assert payload["escalate"] is True
