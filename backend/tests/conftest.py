import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# knowledge_articles.search_vector — PostgreSQL GENERATED ALWAYS AS (TSVECTOR) STORED.
# create_all не создаёт её (нет в ORM-модели), поэтому добавляем вручную
# после create_all только на PostgreSQL. На SQLite-fallback — пропускаем.
_ADD_SEARCH_VECTOR_SQL = sa.text(
    """
    ALTER TABLE knowledge_articles
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('russian'::regconfig,
            coalesce(title, '') || ' ' ||
            coalesce(keywords, '') || ' ' ||
            coalesce(request_type, '')), 'A') ||
        setweight(to_tsvector('simple'::regconfig,
            coalesce(title, '') || ' ' ||
            coalesce(keywords, '') || ' ' ||
            coalesce(request_type, '')), 'A') ||
        setweight(to_tsvector('russian'::regconfig,
            coalesce(problem, '')), 'B') ||
        setweight(to_tsvector('russian'::regconfig,
            coalesce(body, '') || ' ' ||
            coalesce(when_to_escalate, '')), 'C') ||
        setweight(to_tsvector('simple'::regconfig,
            coalesce(search_text, '')), 'D')
    ) STORED
    """
)

_ADD_TICKET_SEARCH_VECTOR_SQL = sa.text(
    """
    ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('russian'::regconfig, coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple'::regconfig, coalesce(title, '')), 'A') ||
        setweight(
            to_tsvector(
                'russian'::regconfig,
                coalesce(body, '') || ' ' ||
                coalesce(request_type, '') || ' ' ||
                coalesce(ai_category, '') || ' ' ||
                coalesce(affected_item, '')
            ),
            'B'
        ) ||
        setweight(
            to_tsvector(
                'russian'::regconfig,
                coalesce(requester_name, '') || ' ' ||
                coalesce(office, '') || ' ' ||
                coalesce(request_details, '') || ' ' ||
                coalesce(requester_email, '')
            ),
            'C'
        )
    ) STORED
    """
)

# Отдельная база для тестов — не трогает рабочие данные.
# По умолчанию используем SQLite, чтобы тесты проходили "из коробки"
# без поднятого Postgres. При необходимости можно переопределить через env:
#   TEST_DATABASE_URL=postgresql+asyncpg://...  (например, в CI)
_TEST_DB_TEMP_DIR = tempfile.TemporaryDirectory(
    prefix=f"support_demo_test_{os.getpid()}_",
    ignore_cleanup_errors=True,
)
_DEFAULT_SQLITE_DB_PATH = Path(_TEST_DB_TEMP_DIR.name) / "test.db"
_DEFAULT_TEST_DATABASE_URL = f"sqlite+aiosqlite:///{_DEFAULT_SQLITE_DB_PATH.as_posix()}"
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", _DEFAULT_TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["PASSWORD_BCRYPT_ROUNDS"] = os.getenv("TEST_PASSWORD_BCRYPT_ROUNDS", "4")

# App imports must happen after DATABASE_URL is forced to the test database.
from app.database import Base, get_db
from app.main import app

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)
TestSessionLocal = async_sessionmaker(bind=test_engine, expire_on_commit=False)


async def _clear_test_database(session: AsyncSession) -> None:
    """Remove rows committed by API handlers so tests stay isolated."""
    for table in reversed(Base.metadata.sorted_tables):
        await session.execute(table.delete())
    await session.commit()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Создаём все таблицы перед тестами, удаляем после.

    Намеренно НЕ используем alembic upgrade head — в тестах важна скорость
    (сотни запусков в день на CI). metadata.create_all создаёт схему за
    один SQL-батч, миграции прогоняли бы каждую revision последовательно.

    Целостность миграций для прода проверяется отдельно в CI — на пустом
    Postgres прогоняется `alembic upgrade head`; см. .github/workflows/ci.yml.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        if "postgresql" in TEST_DATABASE_URL:
            await conn.execute(_ADD_SEARCH_VECTOR_SQL)
            await conn.execute(_ADD_TICKET_SEARCH_VECTOR_SQL)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_ai_service(monkeypatch):
    """Принудительно делаем AI-сервис недостижимым для каждого теста.

    classify_ticket и get_ai_answer немедленно уходят в fallback вместо
    обращения к реальному Ollama / AI-сервису. Тесты становятся
    детерминированными и быстрыми независимо от того, запущен ли Ollama
    в окружении разработчика.

    Тесты, которые явно проверяют поведение AI, могут переопределить
    AI_SERVICE_URL ещё раз через собственный monkeypatch — последний
    вызов побеждает.
    """
    # ai_classifier.py читает URL из модульной переменной, заданной при импорте
    monkeypatch.setattr(
        "app.services.ai_classifier.AI_SERVICE_URL",
        "test://ai-service",
    )
    # conversation_ai.py читает URL из get_settings() в момент вызова
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "AI_SERVICE_URL", "test://ai-service")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Каждый тест стартует с чистыми счётчиками лимитера.

    В обычной жизни tests/ делают десятки запросов с одного и того же
    фейкового IP (127.0.0.1) — без сброса пятый POST /auth/register
    получил бы 429, и каскад тестов развалился бы на ровном месте.

    Тест, который проверяет САМ лимит, делает reset ещё раз в начале,
    чтобы гарантированно стартовать с чистого листа.
    """
    from app.rate_limit import _reset

    _reset()
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Сессия с rollback после каждого теста — тесты изолированы."""
    async with TestSessionLocal() as session:
        from app.services.knowledge_cache import get_knowledge_cache

        get_knowledge_cache().clear()
        await _clear_test_database(session)
        try:
            yield session
        finally:
            await session.rollback()
            get_knowledge_cache().clear()
            await _clear_test_database(session)


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """HTTP-клиент с подменой get_db на тестовую сессию."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
