"""Конфигурация приложения через pydantic-settings.

До этого Settings собирался вручную через os.getenv() — типы приходилось
аннотировать дважды (в __init__ и в class body), а валидация лежала в
__post_init_check__. Миграция на BaseSettings даёт:

- Один источник правды о типе поля (аннотация class body).
- field_validator'ы вместо ad-hoc проверок: pydantic сам бросит
  ValidationError при типе/диапазоне; нам остаётся только бизнес-инварианты.
- SecretStr для JWT_SECRET_KEY и AI_SERVICE_API_KEY: значения не
  светятся в repr/str/log/Sentry (по умолчанию печатается '**********').
  Получить значение можно только через .get_secret_value() — явно.

Чтение значений: settings.JWT_SECRET_KEY теперь SecretStr — берите
через .get_secret_value() в момент использования (см. security.py,
ai_service_client.py).
"""

from functools import lru_cache

from pydantic import (
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Маркер дефолтного небезопасного JWT_SECRET_KEY. В production запрещён —
# разворачиваем self-hosted у клиента, и дефолтный ключ = полная потеря
# безопасности токенов.
_DEFAULT_JWT_SECRET = "supersecretkey_change_in_production"


class Settings(BaseSettings):
    """Конфигурация приложения.

    Читается из переменных окружения и .env-файла (см. SettingsConfigDict
    ниже). Любая переменная за пределами whitelist игнорируется (extra="ignore"),
    чтобы env-leak из CI/Docker не валил приложение на несовместимом ключе.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Окружение ─────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # ── Postgres ──────────────────────────────────────────────────────────
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "app_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: str = "5432"
    DATABASE_URL_OVERRIDE: str | None = Field(default=None, alias="DATABASE_URL")

    # ── AI Service ────────────────────────────────────────────────────────
    AI_SERVICE_URL: str = "http://localhost:8001"
    # SecretStr — чтобы значение не уехало в repr/log/Sentry. Не уверен, что
    # вы доверяете каждому breakpoint'у в IDE и каждому log.exception в проде:
    # SecretStr делает это за вас.
    AI_SERVICE_API_KEY: SecretStr | None = None
    AI_SERVICE_TIMEOUT_SECONDS: float = 180.0
    # Версия модели по умолчанию — fallback для AILog.model_version, когда
    # AI Service по какой-то причине не вернул это поле. Раньше использовался
    # литерал "unknown", но он отравлял датасет для дообучения: разные версии
    # модели сваливались в одну "unknown"-корзину, и метрики по версиям ломались.
    # Теперь fallback — это конкретная строка из .env, которая обновляется
    # вместе с деплоем (например, "mistral-7b-instruct-q4_K_M-2026-04").
    AI_MODEL_VERSION_FALLBACK: str = "mistral-unspecified"

    # ── KB / RAG ──────────────────────────────────────────────────────────
    # По умолчанию включён: код в knowledge_base.py проверяет _pgvector_available()
    # и тихо деградирует на FTS-only, если pgvector в БД не установлен. Так
    # деплои с pgvector получают семантический поиск автоматически, а без него
    # система продолжает работать как раньше (никаких 500-ок).
    KNOWLEDGE_SEMANTIC_SEARCH_ENABLED: bool = True
    KNOWLEDGE_EMBEDDING_DIMENSION: int = 768

    # LLM переформулирует диалог в один поисковый запрос перед обращением
    # к KB. Повышает recall на multi-turn диалогах с уточнениями, но
    # добавляет +1-3 сек латенси на КАЖДОЕ AI-сообщение в чате.
    # По умолчанию OFF — включать после A/B-теста на helped%/recall@1.
    # См. app/services/ai_query_rewrite.py
    KB_QUERY_REWRITE_ENABLED: bool = False

    # Скор у нас вычисляется в _score_article (text_score + context +
    # freshness + feedback) и сильно зависит от: размера KB, длины
    # запросов, веса ts_rank_cd / cosine. На каждом клиенте распределение
    # будет своё, поэтому пороги — конфиг, а не релиз.
    RAG_SCORE_HIGH_THRESHOLD: float = 8.0
    RAG_SCORE_MEDIUM_THRESHOLD: float = 4.0
    # Под этим порогом confidence — «красная зона»: даже если AI не
    # просил эскалацию, мы её принудительно поднимаем (фронт рисует
    # кнопку «Создать тикет»).
    RAG_CONFIDENCE_RED_ZONE: float = 0.6

    # ── JWT ───────────────────────────────────────────────────────────────
    # Секретный ключ — в production должен прийти из переменной окружения,
    # дефолт запрещён валидацией __post_init_check__.
    JWT_SECRET_KEY: SecretStr = SecretStr(_DEFAULT_JWT_SECRET)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60
    PASSWORD_BCRYPT_ROUNDS: int = 12

    # ── Bootstrap-admin ───────────────────────────────────────────────────
    # Первый пользователь с этим email при регистрации получает role=admin.
    # Решает проблему «кто создаст первого админа» в self-hosted.
    BOOTSTRAP_ADMIN_EMAIL: str | None = None

    # ── CORS ──────────────────────────────────────────────────────────────
    # Список origin'ов через запятую, откуда браузер может стучать на API.
    # Парсится через property CORS_ORIGINS, потому что встроенная JSON-
    # поддержка pydantic-settings ожидала бы '["x", "y"]', а у нас просто
    # строка из docker-compose: 'http://x,http://y'.
    CORS_ORIGINS_RAW: str = Field(default="", alias="CORS_ORIGINS")

    # ── Workers ───────────────────────────────────────────────────────────
    # Через сколько секунд running-задача считается зависшей. Используется
    # одновременно воркером (для авто-перевешивания в очередь) и API
    # (для is_stale-флага в ответе /jobs). Значение должно быть единым,
    # иначе UI и воркер начнут расходиться: оператор увидит «зависла»
    # на здоровой задаче или, наоборот, не увидит на реально зависшей.
    AI_WORKER_STALE_RUNNING_SECONDS: int = 600
    KNOWLEDGE_EMBEDDING_WORKER_STALE_RUNNING_SECONDS: int = 900

    # ── SMTP (email-уведомления) ──────────────────────────────────────────────
    # Если SMTP_HOST не задан — уведомления по email отключены (no-op).
    # Поддерживается STARTTLS (587) и SMTPS/SSL (465). Для внутреннего relay
    # без TLS установите SMTP_USE_TLS=false.
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_FROM: str = "noreply@support.local"
    SMTP_USE_TLS: bool = True

    # ── Retention логов ───────────────────────────────────────────────────────
    # Количество дней хранения audit_logs, ai_fallback_events и завершённых
    # ai_jobs / knowledge_embedding_jobs. 0 — retention отключён (хранить всё).
    LOG_RETENTION_DAYS: int = 90

    # ── Rate limiter ──────────────────────────────────────────────────────
    # memory  — счётчики в памяти процесса, по uvicorn-воркеру свои.
    # redis   — общий счётчик через ZSET-sliding-window на REDIS_URL.
    RATE_LIMIT_BACKEND: str = "memory"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Сколько доверенных прокси стоит перед приложением.
    # 0  — прямой доступ, IP берётся из request.client.host.
    # N  — N прокси (nginx, L7-балансировщик); реальный IP клиента
    #      извлекается из X-Forwarded-For, отбрасывая N последних записей.
    # Пример nginx→app: X-Forwarded-For: 1.2.3.4 → TRUSTED_PROXY_COUNT=1 → IP=1.2.3.4
    # ВАЖНО: устанавливайте > 0 только если прокси контролируется вами —
    # иначе любой клиент подделает заголовок и обойдёт лимит.
    TRUSTED_PROXY_COUNT: int = 0

    # ── Поле для ленивых вычислений (без алиасов в env) ──────────────────

    @property
    def CORS_ORIGINS(self) -> list[str]:
        """Парсит CORS_ORIGINS_RAW в список строк.

        Пустая строка → пустой список (CORS выключен).
        Пробелы вокруг origin'ов обрезаются.
        """
        raw = self.CORS_ORIGINS_RAW.strip()
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def DATABASE_URL(self) -> str:
        """URL подключения к Postgres.

        Прямой override через DATABASE_URL имеет приоритет — пригождается:
          - в тестах (sqlite+aiosqlite),
          - в Alembic-миграциях против тестовой БД,
          - в staging-окружении с внешним URL (RDS, Supabase).

        Иначе собирается из POSTGRES_* — штатный путь для docker-compose.
        """
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── Валидаторы ────────────────────────────────────────────────────────

    @field_validator("RATE_LIMIT_BACKEND")
    @classmethod
    def _normalize_rate_limit_backend(cls, value: str) -> str:
        # Опечатка вместо memory/redis — открытый /auth/login для брутфорса.
        # Это явная ошибка в .env, валим на старте с понятным текстом.
        normalized = value.strip().lower()
        if normalized not in {"memory", "redis"}:
            raise ValueError(
                f"RATE_LIMIT_BACKEND={value!r} не поддерживается. Допустимы 'memory' и 'redis'."
            )
        return normalized

    @field_validator(
        "AI_WORKER_STALE_RUNNING_SECONDS",
        "KNOWLEDGE_EMBEDDING_WORKER_STALE_RUNNING_SECONDS",
    )
    @classmethod
    def _validate_worker_stale_running_seconds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Worker stale running timeout must be greater than 0.")
        return value

    @field_validator("PASSWORD_BCRYPT_ROUNDS")
    @classmethod
    def _validate_password_bcrypt_rounds(cls, value: int) -> int:
        if not 4 <= value <= 31:
            raise ValueError("PASSWORD_BCRYPT_ROUNDS must be between 4 and 31.")
        return value

    @model_validator(mode="after")
    def __post_init_check__(self) -> "Settings":
        """Кросс-полевые инварианты + проверки production-секретов.

        Все правила собраны в одном месте, а не разбиты по field_validator'ам:
        тесты конструируют Settings(), правят пару полей и зовут эту функцию
        напрямую, чтобы зафиксировать конкретное правило в изоляции.
        """
        # 1) RAG-пороги: оба > 0 и MEDIUM <= HIGH.
        if self.RAG_SCORE_HIGH_THRESHOLD <= 0 or self.RAG_SCORE_MEDIUM_THRESHOLD <= 0:
            raise RuntimeError(
                "RAG_SCORE_HIGH_THRESHOLD и RAG_SCORE_MEDIUM_THRESHOLD должны быть > 0."
            )
        if self.RAG_SCORE_MEDIUM_THRESHOLD > self.RAG_SCORE_HIGH_THRESHOLD:
            raise RuntimeError(
                f"RAG_SCORE_MEDIUM_THRESHOLD ({self.RAG_SCORE_MEDIUM_THRESHOLD}) "
                f"не может быть больше RAG_SCORE_HIGH_THRESHOLD ({self.RAG_SCORE_HIGH_THRESHOLD}). "
                "Иначе ответы из KB будут отдавать «answer», когда уверенности нет."
            )

        # 2) RAG red-zone: confidence модели нормирован в [0, 1], порог за
        # пределами этого диапазона либо никогда не сработает, либо всегда —
        # оба случая = ошибка конфига.
        if not 0.0 <= self.RAG_CONFIDENCE_RED_ZONE <= 1.0:
            raise RuntimeError(
                f"RAG_CONFIDENCE_RED_ZONE ({self.RAG_CONFIDENCE_RED_ZONE}) должен быть в [0, 1]: "
                "это порог по confidence модели, который сам нормирован в этом диапазоне."
            )

        # 3) JWT_SECRET_KEY: дефолтное значение запрещено в production.
        if (
            self.APP_ENV == "production"
            and self.JWT_SECRET_KEY.get_secret_value() == _DEFAULT_JWT_SECRET
        ):
            raise RuntimeError(
                "JWT_SECRET_KEY не задан в .env при APP_ENV=production. "
                "Сгенерируй длинную случайную строку и положи в переменные окружения."
            )

        if self.APP_ENV == "production" and self.PASSWORD_BCRYPT_ROUNDS < 12:
            raise RuntimeError(
                "PASSWORD_BCRYPT_ROUNDS must be at least 12 when APP_ENV=production."
            )

        # 4) AI_SERVICE_API_KEY: симметрично с ai-service. Без ключа в проде
        # связка backend↔ai-service гарантированно сломана — лучше упасть на
        # старте, чем на первом /ai/answer.
        if self.APP_ENV == "production" and self.AI_SERVICE_API_KEY is None:
            raise RuntimeError(
                "AI_SERVICE_API_KEY не задан при APP_ENV=production. "
                "Без него запросы к AI-сервису не аутентифицируются и будут отклонены."
            )

        # 5) RATE_LIMIT_BACKEND: дублирующая проверка для случая ручного
        # присваивания (field_validator срабатывает только при создании
        # Settings из env). Без validate_assignment=True это единственная
        # точка перехвата — а опечатка молча превратит лимитер в no-op.
        if self.RATE_LIMIT_BACKEND not in {"memory", "redis"}:
            raise RuntimeError(
                f"RATE_LIMIT_BACKEND={self.RATE_LIMIT_BACKEND!r} не поддерживается. "
                "Допустимы 'memory' и 'redis'."
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
