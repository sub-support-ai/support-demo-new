import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401 — регистрирует все ORM-модели в Base.metadata
from app.config import get_settings
from app.context import request_id_ctx
from app.database import engine, get_db
from app.logging_config import setup_logging
from app.metrics import setup_metrics
from app.routers.assets import router as assets_router
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.automation_rules import router as automation_rules_router
from app.routers.conversations import router as conversations_router
from app.routers.jobs import router as jobs_router
from app.routers.knowledge_articles import router as knowledge_articles_router
from app.routers.notifications import router as notifications_router
from app.routers.response_templates import router as response_templates_router
from app.routers.stats import router as stats_router
from app.routers.tickets import router as tickets_router
from app.routers.users import router as users_router
from app.sentry_config import setup_sentry

setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_sentry()
    # ВАЖНО: схема БД создаётся и обновляется ТОЛЬКО через Alembic-миграции.
    # Перед первым запуском и после каждого git pull у клиента:
    #   alembic upgrade head
    # Мы не вызываем Base.metadata.create_all здесь, потому что:
    #   1. create_all не применит изменения к существующим таблицам
    #      → на втором релизе клиент получит "column does not exist".
    #   2. Параллельный старт нескольких инстансов → гонка на CREATE TABLE.
    #   3. Alembic хранит версию схемы в alembic_version → отслеживаемость.
    #
    # Тесты создают таблицы через metadata.create_all — это быстрее, и
    # в тестовой SQLite-базе миграции не нужны (см. tests/conftest.py).
    logger.info("Приложение запускается — сервер готов")
    yield
    logger.info("Сервер останавливается — закрываем соединения с БД")
    await engine.dispose()


app = FastAPI(
    title="Support Tickets API",
    description=(
        "AI-powered система обработки обращений пользователей.\n\n"
        "**Авторизация:** все эндпоинты (кроме /healthcheck, /auth/register, /auth/login) "
        "требуют заголовок `Authorization: Bearer <token>`.\n\n"
        "**Роли:** DELETE /tickets только для `role=admin`."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

setup_metrics(app)
# ── CORS ──────────────────────────────────────────────────────────────────────
# Подключаем только если список origins задан в .env. Пустой список → не
# регистрируем middleware (сокращает overhead и риск "accidentally allow all").
#
# allow_credentials=True означает, что браузер будет слать Authorization-заголовок
# или cookies при запросах. В паре с allow_origins=["*"] это запрещено стандартом
# (браузер сам откажет) — поэтому мы принципиально не поддерживаем "*".
_settings = get_settings()
if _settings.CORS_ORIGINS:
    # Regex-паттерн принимает любые туннельные домены для локальной демонстрации
    # (localhost.run, trycloudflare.com и т.п.) — URL меняется при каждом
    # переподключении туннеля, поэтому фиксированный список неудобен.
    # В production замените на строгий список через CORS_ORIGINS.
    _tunnel_regex = (
        r"https://[a-z0-9\-]+\.(localhost\.run|trycloudflare\.com|loca\.lt|ngrok-free\.app|ngrok\.io)"
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.CORS_ORIGINS,
        allow_origin_regex=_tunnel_regex,
        allow_credentials=True,
        allow_methods=["*"],  # GET, POST, PATCH, DELETE, OPTIONS — для preflight
        allow_headers=["*"],  # в т.ч. Authorization, Content-Type
    )
    logger.info("CORS middleware подключён", extra={"origins": _settings.CORS_ORIGINS})
else:
    logger.info("CORS_ORIGINS пуст — CORS middleware отключён")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception — возможно отсутствует CORS на 500",
        extra={"path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    # Устанавливаем ПЕРЕД call_next: все корутины в рамках этого запроса
    # наследуют значение через copy-on-create семантику contextvars.
    # Это позволяет любому сервису и воркеру прочитать rid без передачи
    # через параметры, а logging-фильтр подмешивает его в каждую строку лога.
    token = request_id_ctx.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.exception(
            "HTTP request failed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration_ms,
            },
        )
        raise
    finally:
        request_id_ctx.reset(token)

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "HTTP request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


app.include_router(auth_router, prefix="/api/v1")
app.include_router(assets_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(stats_router, prefix="/api/v1")
app.include_router(tickets_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(knowledge_articles_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(response_templates_router, prefix="/api/v1")
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(automation_rules_router, prefix="/api/v1")


@app.get("/healthcheck", tags=["system"])
async def healthcheck(db: AsyncSession = Depends(get_db)):
    """
    Liveness + readiness в одном эндпоинте.

    Почему важно пинговать БД:
      - Kubernetes/Docker вызывают /healthcheck и решают, слать ли трафик.
      - Если приложение живо, но Postgres упал — клиенты получат 500 на
        каждый запрос, но балансировщик будет видеть "зелёный".
      - SELECT 1 — дешевле любой реальной таблицы, но честно проверяет,
        что соединение живо и коннект из пула работает.

    Возвращает 503 если БД недоступна — балансировщик перестанет слать
    трафик на этот инстанс.
    """
    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        logger.exception("Healthcheck: БД недоступна")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database_unavailable",
        ) from e
    return {"status": "ok", "database": "ok"}
