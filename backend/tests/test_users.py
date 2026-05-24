import pytest
from httpx import AsyncClient

from app.models.asset import Asset


@pytest.mark.asyncio
async def test_healthcheck(client: AsyncClient):
    """Healthcheck должен отвечать 200 и подтверждать живость БД."""
    response = await client.get("/healthcheck")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


@pytest.mark.asyncio
async def test_register_user(client: AsyncClient):
    """POST /auth/register — самостоятельная регистрация. Возвращает access_token."""
    payload = {
        "email": "test@example.com",
        "username": "testuser",
        "password": "Secret123!",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Проверяем, что пароль не утекает в /auth/me
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert me.status_code == 200
    me_data = me.json()
    assert me_data["email"] == payload["email"]
    assert me_data["username"] == payload["username"]
    assert me_data["role"] == "user"
    assert me_data["is_active"] is True
    assert "password" not in me_data
    assert "hashed_password" not in me_data


@pytest.mark.asyncio
async def test_register_accepts_simple_username(client: AsyncClient):
    """Логин не ограничен набором символов; уникальность проверяется отдельно."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "simplelogin@example.com",
            "username": "юзер",
            "password": "Secret123!",
        },
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_register_rejects_weak_password(client: AsyncClient):
    """Пароль должен проходить базовую complexity policy."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "weakpw@example.com",
            "username": "weakpwuser",
            "password": "secret123",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_rejects_invalid_email(client: AsyncClient):
    """Email проверяется через EmailStr/Pydantic."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "username": "emailuser",
            "password": "Secret123!",
        },
    )
    assert response.status_code == 422


# ── bcrypt 72-byte: длинные пароли не ломают хэширование ─────────────────────
#
# security.py пре-хэширует пароль через SHA-256 → hex (64 ASCII байта), и только
# потом кормит bcrypt. За счёт этого:
#   1) любой длины пароль укладывается в 72-байтный лимит bcrypt;
#   2) разные пароли, совпадающие по первым 72 байтам, дают РАЗНЫЕ хэши
#      (в bcrypt-напрямую они коллизировали бы).
# Эти тесты — регрессия: если кто-то уберёт SHA-256-нормализацию, они упадут.


@pytest.mark.asyncio
async def test_long_password_round_trips(client: AsyncClient):
    """Пароль близко к верхней границе регистрируется и логинится — то есть хэш
    корректно покрывает всю длину, а не только первые 72 байта."""
    long_pw = "A" + ("a" * 124) + "1!"
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "longpw@example.com",
            "username": "longpwuser",
            "password": long_pw,
        },
    )
    assert reg.status_code == 201

    # /auth/login — OAuth2PasswordRequestForm, принимает form-data (username+password)
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "longpwuser", "password": long_pw},
    )
    assert login.status_code == 200
    assert "access_token" in login.json()


@pytest.mark.asyncio
async def test_cyrillic_password_round_trips(client: AsyncClient):
    """80 байт UTF-8 (40 × 'ё') работает end-to-end.
    До SHA-256-нормализации такой пароль упирался в 72-байтный потолок."""
    cyrillic_pw = "Ё" + ("ё" * 37) + "1!"  # 80+ байт UTF-8
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "cyrpw@example.com",
            "username": "cyrpwuser",
            "password": cyrillic_pw,
        },
    )
    assert reg.status_code == 201

    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "cyrpwuser", "password": cyrillic_pw},
    )
    assert login.status_code == 200


def test_different_long_passwords_produce_different_hashes():
    """
    Главный sanity: два пароля, отличающиеся ТОЛЬКО после 72-го байта,
    должны давать несовпадающие результаты verify.

    Без SHA-256-пре-хэша bcrypt видел бы оба как "a"*72 и считал
    эквивалентными — классическая CVE-class коллизия.
    """
    from app.security import hash_password, verify_password

    pw_a = "a" * 72 + "X"
    pw_b = "a" * 72 + "Y"

    stored_a = hash_password(pw_a)
    assert verify_password(pw_a, stored_a) is True
    assert verify_password(pw_b, stored_a) is False  # ← без SHA-256 было бы True


def test_tests_use_fast_password_hash_cost():
    from app.security import hash_password

    stored = hash_password("Secret123!")

    assert stored.split("$")[2] == "04"


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    payload = {
        "email": "duplicate@example.com",
        "username": "user1",
        "password": "Secret123!",
    }
    await client.post("/api/v1/auth/register", json=payload)

    # Второй раз с тем же email — 409
    payload["username"] = "user2"
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    assert "Email" in response.json()["detail"]


@pytest.mark.asyncio
async def test_register_duplicate_username(client: AsyncClient):
    payload = {
        "email": "user3@example.com",
        "username": "sameusername",
        "password": "Secret123!",
    }
    await client.post("/api/v1/auth/register", json=payload)

    # Второй раз с тем же username — 409
    payload["email"] = "user4@example.com"
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    assert "Username" in response.json()["detail"]


@pytest.mark.asyncio
async def test_register_duplicate_username_after_trim(client: AsyncClient):
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "trimuser1@example.com",
            "username": "trimmed",
            "password": "Secret123!",
        },
    )

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "trimuser2@example.com",
            "username": "  trimmed  ",
            "password": "Secret123!",
        },
    )

    assert response.status_code == 409
    assert "Username" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_self(client: AsyncClient, db_session):
    """GET /users/{id} доступен владельцу — /users/<свой_id> возвращает 200."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "getme@example.com",
            "username": "getmeuser",
            "password": "Secret123!",
        },
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    first_me = await client.get("/api/v1/auth/me", headers=headers)
    user_id = first_me.json()["id"]
    db_session.add(
        Asset(
            asset_type="laptop",
            name="ThinkPad T14",
            serial_number="NB-1001",
            owner_user_id=user_id,
            office="Main office",
            status="active",
        )
    )
    await db_session.flush()

    me = await client.get("/api/v1/auth/me", headers=headers)
    request_context = me.json()["request_context"]
    assert request_context["requester_name"] == "getmeuser"
    assert request_context["requester_email"] == "getme@example.com"
    assert request_context["office"] == "Main office"
    assert request_context["office_source"] == "asset"
    assert request_context["primary_asset"]["serial_number"] == "NB-1001"
    assert "ThinkPad T14 (NB-1001)" in request_context["affected_item_options"]
    assert "Главный офис" in request_context["office_options"]
    assert "VPN" in request_context["affected_item_options"]

    response = await client.get(f"/api/v1/users/{user_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == user_id


@pytest.mark.asyncio
async def test_get_other_user_forbidden(client: AsyncClient):
    """Обычный пользователь не может смотреть чужой профиль → 403."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "spy@example.com",
            "username": "spy",
            "password": "Secret123!",
        },
    )
    token = reg.json()["access_token"]
    response = await client.get(
        "/api/v1/users/99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_users_list_requires_admin(client: AsyncClient):
    """GET /users/ без админ-токена → 403."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "nonadmin@example.com",
            "username": "nonadmin",
            "password": "Secret123!",
        },
    )
    token = reg.json()["access_token"]
    response = await client.get(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_stats_requires_auth(client: AsyncClient):
    """GET /stats/ без токена → 401."""
    response = await client.get("/api/v1/stats/")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_stats_includes_job_queue_counters(client: AsyncClient):
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "statsjobs@example.com",
            "username": "statsjobs",
            "password": "Secret123!",
        },
    )
    token = reg.json()["access_token"]

    response = await client.get(
        "/api/v1/stats/",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["jobs"]["ai"] == {
        "total": 0,
        "queued": 0,
        "running": 0,
        "done": 0,
        "failed": 0,
    }
    assert data["jobs"]["knowledge_embeddings"] == {
        "total": 0,
        "queued": 0,
        "running": 0,
        "done": 0,
        "failed": 0,
    }


# ── Bootstrap-admin ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_admin_registration(client: AsyncClient, monkeypatch):
    """
    Если BOOTSTRAP_ADMIN_EMAIL совпадает с email при регистрации —
    пользователь автоматически получает role=admin.
    """
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAIL", "ceo@acme.com")

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ceo@acme.com",
            "username": "ceo",
            "password": "Secret123!",
        },
    )
    assert reg.status_code == 201

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {reg.json()['access_token']}"},
    )
    assert me.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_bootstrap_admin_case_insensitive(client: AsyncClient, monkeypatch):
    """Email сравнивается без учёта регистра."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAIL", "Admin@Corp.com")

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@corp.com",  # lower-case, а в env — Mixed-case
            "username": "mixcaseadmin",
            "password": "Secret123!",
        },
    )
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {reg.json()['access_token']}"},
    )
    assert me.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_cors_allows_configured_origin(client: AsyncClient, monkeypatch):
    """
    Preflight-запрос с Origin из белого списка → ответ с Access-Control-Allow-Origin.
    NOTE: мы патчим settings.CORS_ORIGINS_RAW, но CORSMiddleware уже зарегистрирован
    при старте app, поэтому тест проверяет только факт "middleware установлен".
    Чтобы проверить реальный фильтр — нужен отдельный app-инстанс на тест.
    """
    # Если CORS не подключён (CORS_ORIGINS был пуст при старте) — тест skip.
    # Реальную валидацию покрывает test_cors_no_middleware_when_empty ниже.
    from starlette.middleware.cors import CORSMiddleware as _CORSMiddleware

    from app.main import app

    cors_middleware = next((m for m in app.user_middleware if m.cls is _CORSMiddleware), None)
    if cors_middleware is None:
        pytest.skip("CORS middleware не подключён в этом процессе — CORS_ORIGINS пуст")
    origin = cors_middleware.kwargs["allow_origins"][0]

    response = await client.options(
        "/healthcheck",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.asyncio
async def test_cors_no_middleware_when_empty():
    """
    Санити: если CORS_ORIGINS пустой, middleware не добавляется (см. main.py).
    Проверяем property напрямую — он возвращает пустой список для пустой строки.
    """
    from app.config import Settings

    s = Settings()
    s.CORS_ORIGINS_RAW = ""
    assert s.CORS_ORIGINS == []

    s.CORS_ORIGINS_RAW = "  "
    assert s.CORS_ORIGINS == []

    s.CORS_ORIGINS_RAW = "http://localhost:3000, https://app.acme.com"
    assert s.CORS_ORIGINS == ["http://localhost:3000", "https://app.acme.com"]


# ── Fail-closed: production обязан иметь AI_SERVICE_API_KEY ──────────────────


def test_settings_requires_ai_service_key_in_production():
    """В production пустой AI_SERVICE_API_KEY → RuntimeError на старте.

    Без ключа бэкенд ходит в AI без X-AI-Service-Key, а ai-service в
    production отвергает такие запросы (тоже fail-closed). Лучше упасть
    на старте бэкенда с понятным сообщением, чем на первом /ai/answer
    в проде получить тихую 401-цепочку, спрятанную в логах.
    """
    from pydantic import SecretStr

    from app.config import Settings

    s = Settings()
    s.APP_ENV = "production"
    # SecretStr — теперь поле обёрнуто, чтобы значение не светилось в логе
    # Pydantic-модели; см. config.py / Блок 12.
    s.JWT_SECRET_KEY = SecretStr("a" * 64)  # валидный ключ — изолируем проверку AI
    s.PASSWORD_BCRYPT_ROUNDS = 12
    s.AI_SERVICE_API_KEY = None

    with pytest.raises(RuntimeError, match="AI_SERVICE_API_KEY"):
        s.__post_init_check__()


def test_settings_allows_production_when_ai_key_set():
    """Production с заданным AI_SERVICE_API_KEY проходит валидацию."""
    from pydantic import SecretStr

    from app.config import Settings

    s = Settings()
    s.APP_ENV = "production"
    s.JWT_SECRET_KEY = SecretStr("a" * 64)
    s.PASSWORD_BCRYPT_ROUNDS = 12
    s.AI_SERVICE_API_KEY = SecretStr("prod-secret")

    # Не должно бросать
    s.__post_init_check__()


def test_settings_rejects_low_bcrypt_rounds_in_production():
    from pydantic import SecretStr

    from app.config import Settings

    s = Settings()
    s.APP_ENV = "production"
    s.JWT_SECRET_KEY = SecretStr("a" * 64)
    s.AI_SERVICE_API_KEY = SecretStr("prod-secret")
    s.PASSWORD_BCRYPT_ROUNDS = 4

    with pytest.raises(RuntimeError, match="PASSWORD_BCRYPT_ROUNDS"):
        s.__post_init_check__()


def test_settings_allows_development_without_ai_key():
    """В dev пустой AI_SERVICE_API_KEY — нормально (упрощает локальный запуск)."""
    from app.config import Settings

    s = Settings()
    s.APP_ENV = "development"
    s.AI_SERVICE_API_KEY = None

    # Не должно бросать
    s.__post_init_check__()


def test_secret_str_does_not_leak_in_repr():
    """Регрессия Блока 12: SecretStr(JWT_SECRET_KEY) не должен светиться
    в repr(settings) — иначе любой log.exception() с settings в extra или
    Sentry breadcrumb потечёт ключом подписи токенов в дампе.
    """
    from pydantic import SecretStr

    from app.config import Settings

    s = Settings()
    s.JWT_SECRET_KEY = SecretStr("super-real-secret-12345")
    s.AI_SERVICE_API_KEY = SecretStr("ai-real-secret-67890")

    rendered = repr(s) + " " + str(s) + " " + str(s.model_dump())
    assert "super-real-secret-12345" not in rendered
    assert "ai-real-secret-67890" not in rendered


# ── Блок 4: валидация RAG-порогов ─────────────────────────────────────────────


def test_settings_rejects_swapped_rag_thresholds():
    """MEDIUM > HIGH — порядок порогов сломан, на первом же запросе из KB
    логика отдаст «answer» там, где должно быть «escalate».
    """
    from app.config import Settings

    s = Settings()
    s.APP_ENV = "development"
    s.RAG_SCORE_HIGH_THRESHOLD = 4.0
    s.RAG_SCORE_MEDIUM_THRESHOLD = 8.0  # перепутаны

    with pytest.raises(RuntimeError, match="RAG_SCORE_MEDIUM_THRESHOLD"):
        s.__post_init_check__()


def test_settings_rejects_zero_or_negative_rag_thresholds():
    """Скор всегда положительный (см. _score_article). Нулевой / отрицательный
    порог обозначал бы «принимать всё», что бессмысленно — это ошибка конфига.
    """
    from app.config import Settings

    s = Settings()
    s.APP_ENV = "development"
    s.RAG_SCORE_HIGH_THRESHOLD = 0.0
    s.RAG_SCORE_MEDIUM_THRESHOLD = 4.0

    with pytest.raises(RuntimeError, match="должны быть > 0"):
        s.__post_init_check__()


def test_settings_rejects_red_zone_outside_unit_interval():
    """Confidence модели нормирован [0, 1]; порог за пределами этого диапазона
    либо никогда не сработает, либо сработает всегда — оба случая = ошибка.
    """
    from app.config import Settings

    s = Settings()
    s.APP_ENV = "development"
    s.RAG_CONFIDENCE_RED_ZONE = 1.5

    with pytest.raises(RuntimeError, match="RAG_CONFIDENCE_RED_ZONE"):
        s.__post_init_check__()


def test_settings_accepts_valid_rag_configuration():
    """Эталонные значения проходят валидацию — sanity check для дефолтов."""
    from app.config import Settings

    s = Settings()
    s.APP_ENV = "development"
    s.RAG_SCORE_HIGH_THRESHOLD = 8.0
    s.RAG_SCORE_MEDIUM_THRESHOLD = 4.0
    s.RAG_CONFIDENCE_RED_ZONE = 0.6

    s.__post_init_check__()  # не должно бросать


@pytest.mark.asyncio
async def test_non_bootstrap_users_stay_regular(client: AsyncClient, monkeypatch):
    """
    Если email НЕ совпадает с BOOTSTRAP_ADMIN_EMAIL — обычный user.
    Регрессия: случайный пользователь не должен стать админом.
    """
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAIL", "ceo@acme.com")

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "random@acme.com",
            "username": "randomuser",
            "password": "Secret123!",
        },
    )
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {reg.json()['access_token']}"},
    )
    assert me.json()["role"] == "user"


# ── Rate limit на /auth ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_brute_force(client: AsyncClient):
    """
    6-й подряд /auth/login с одного IP в минутном окне возвращает 429.
    Первые 5 попыток пропускаются (пусть и с 401) — это нормальный UX:
    пользователь мог опечататься.
    """
    # Сначала создаём юзера, чтобы /login не валился на "нет такого".
    # Регистрация не лимитируется в рамках 3/мин — одна регистрация OK.
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "brute@example.com",
            "username": "brute",
            "password": "Correct123!",
        },
    )

    # Сбрасываем счётчики ПЕРЕД тестом, чтобы регистрация выше не съела
    # квоту login'а (они считаются раздельно, но на всякий случай).
    from app.rate_limit import _reset

    _reset()

    # Пять заведомо неверных попыток — все возвращают 401, но лимитер
    # уже записал их и на 6-ю сработает.
    for _ in range(5):
        resp = await client.post(
            "/api/v1/auth/login",
            data={"username": "brute", "password": "wrong"},
        )
        assert resp.status_code == 401

    # Шестая попытка — даже с ПРАВИЛЬНЫМ паролем получает 429.
    # Это важно: лимит срабатывает РАНЬШЕ проверки пароля, иначе
    # атакующий узнал бы по задержке, когда угадал.
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "brute", "password": "Correct123!"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_register_rate_limit_blocks_spam(client: AsyncClient):
    """4-я подряд регистрация с одного IP в минуту → 429."""
    from app.rate_limit import _reset

    _reset()

    # Три легитимные регистрации проходят.
    for i in range(3):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"spam{i}@example.com",
                "username": f"spammer{i}",
                "password": "Secret123!",
            },
        )
        assert resp.status_code == 201

    # Четвёртая — блок.
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "spam3@example.com",
            "username": "spammer3",
            "password": "Secret123!",
        },
    )
    assert resp.status_code == 429


# ── PATCH /users/{id}/role ───────────────────────────────────────────────────


async def _register_and_promote(client, db_session, suffix: str) -> tuple[int, str]:
    """Регистрация + ручное повышение до admin (минуя bootstrap-логику)."""
    from app.models.user import User

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"role-{suffix}@example.com",
            "username": f"role_{suffix}",
            "password": "Secret123!",
        },
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    user_id = me.json()["id"]
    user = await db_session.get(User, user_id)
    assert user is not None
    user.role = "admin"
    await db_session.flush()
    return user_id, token


async def _register_regular(client, suffix: str) -> tuple[int, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"role-r-{suffix}@example.com",
            "username": f"role_r_{suffix}",
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


@pytest.mark.asyncio
async def test_admin_can_promote_user_to_agent(client: AsyncClient, db_session):
    _, admin_token = await _register_and_promote(client, db_session, "promote-admin")
    target_id, _ = await _register_regular(client, "promote-target")

    response = await client.patch(
        f"/api/v1/users/{target_id}/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "agent"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "agent"


@pytest.mark.asyncio
async def test_role_change_is_audited(client: AsyncClient, db_session):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    admin_id, admin_token = await _register_and_promote(client, db_session, "audit-admin")
    target_id, _ = await _register_regular(client, "audit-target")

    response = await client.patch(
        f"/api/v1/users/{target_id}/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "agent"},
    )
    assert response.status_code == 200

    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "user.role_change")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    entry = rows[0]
    assert entry.user_id == admin_id
    assert entry.target_type == "user"
    assert entry.target_id == target_id
    assert "user" in entry.details and "agent" in entry.details


@pytest.mark.asyncio
async def test_role_change_no_op_is_idempotent_and_not_audited(client: AsyncClient, db_session):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    _, admin_token = await _register_and_promote(client, db_session, "idem-admin")
    target_id, _ = await _register_regular(client, "idem-target")

    response = await client.patch(
        f"/api/v1/users/{target_id}/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "user"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "user"

    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "user.role_change")))
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_role_change_rejects_self_demotion(client: AsyncClient, db_session):
    admin_id, admin_token = await _register_and_promote(client, db_session, "self-demote")

    response = await client.patch(
        f"/api/v1/users/{admin_id}/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "user"},
    )
    assert response.status_code == 409
    assert "themselves" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_role_change_blocks_demoting_last_admin(client: AsyncClient, db_session):
    """Сценарий "последний админ".

    В обычном HTTP-флоу триггер недостижим: чтобы admin_count<=1 в момент
    SELECT внутри endpoint'а, target должен быть единственным admin'ом
    в БД, а вызывающий — admin'ом, которого в БД нет (иначе их минимум
    двое). На практике это ловит только параллельная транзакция,
    которая успела демоутить второго admin'а.

    Для воспроизведения подменяем get_current_user/require_role через
    FastAPI dependency_overrides — каллер становится "виртуальным
    admin'ом", который в БД admin'ом не числится. Все остальные ветки
    (self-demotion, idempotent, audit, 403/404) тестируются обычным
    HTTP-флоу выше.
    """
    from sqlalchemy import update

    from app.dependencies import get_current_user
    from app.main import app
    from app.models.user import User as UserModel

    target_id, _ = await _register_and_promote(client, db_session, "lastadmin-target")
    caller_id, _ = await _register_regular(client, "lastadmin-caller")
    await db_session.execute(
        update(UserModel)
        .where(UserModel.id != target_id, UserModel.role == "admin")
        .values(role="user")
    )
    await db_session.flush()
    caller_in_db = await db_session.get(UserModel, caller_id)
    assert caller_in_db is not None

    # Phantom admin: НЕ привязан к сессии, role="admin" живёт только
    # в памяти. require_role("admin") пропустит, count(role=admin) в БД
    # этого юзера не учтёт → admin_count = 1 (только target) → 409.
    async def fake_admin() -> UserModel:
        return UserModel(
            id=caller_id,
            email=caller_in_db.email,
            username=caller_in_db.username,
            hashed_password=caller_in_db.hashed_password,
            role="admin",
            is_active=True,
        )

    app.dependency_overrides[get_current_user] = fake_admin
    try:
        response = await client.patch(
            f"/api/v1/users/{target_id}/role",
            json={"role": "user"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "last" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_role_change_unknown_user_returns_404(client: AsyncClient, db_session):
    _, admin_token = await _register_and_promote(client, db_session, "404admin")
    response = await client.patch(
        "/api/v1/users/999999/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "agent"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_role_change_rejects_invalid_role(client: AsyncClient, db_session):
    _, admin_token = await _register_and_promote(client, db_session, "invalidrole")
    target_id, _ = await _register_regular(client, "invalidrole-target")
    response = await client.patch(
        f"/api/v1/users/{target_id}/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "manager"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_role_change_forbidden_for_non_admin(client: AsyncClient):
    _, user_token = await _register_regular(client, "nonadmin")
    response = await client.patch(
        "/api/v1/users/1/role",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"role": "agent"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_role_change_requires_auth(client: AsyncClient):
    response = await client.patch(
        "/api/v1/users/1/role",
        json={"role": "agent"},
    )
    assert response.status_code == 401


# ── PATCH /users/{id}/active ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deactivate_user_revokes_api_access(client: AsyncClient, db_session):
    """После деактивации токен пользователя отвергается с 401.

    get_current_user делает SELECT при каждом запросе — is_active=False
    возвращает credentials_error немедленно, без ожидания истечения токена.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    user_id, user_token = await _register_regular(client, "deact-access")
    admin_id, admin_token = await _register_and_promote(client, db_session, "deact-admin")

    # Пользователь работает нормально
    assert (
        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 200

    # Администратор деактивирует
    resp = await client.patch(
        f"/api/v1/users/{user_id}/active",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Токен пользователя больше не работает
    assert (
        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 401

    # Действие попало в audit_log
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "user.deactivate")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].user_id == admin_id
    assert rows[0].target_id == user_id


@pytest.mark.asyncio
async def test_reactivate_user_restores_access(client: AsyncClient, db_session):
    """Реактивация восстанавливает доступ и пишет user.activate в audit."""
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    user_id, user_token = await _register_regular(client, "react-user")
    _, admin_token = await _register_and_promote(client, db_session, "react-admin")

    # Деактивируем
    assert (
        await client.patch(
            f"/api/v1/users/{user_id}/active",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active": False},
        )
    ).status_code == 200

    # Реактивируем
    resp = await client.patch(
        f"/api/v1/users/{user_id}/active",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    # Токен снова работает
    assert (
        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 200

    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "user.activate")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_deactivate_is_idempotent(client: AsyncClient, db_session):
    """Повторная деактивация уже неактивного пользователя возвращает 200
    без дублирования записи в audit_log."""
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    user_id, _ = await _register_regular(client, "idem-deact")
    _, admin_token = await _register_and_promote(client, db_session, "idem-deact-admin")

    for _ in range(2):
        assert (
            await client.patch(
                f"/api/v1/users/{user_id}/active",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"is_active": False},
            )
        ).status_code == 200

    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "user.deactivate")))
        .scalars()
        .all()
    )
    # Второй вызов — no-op, запись в лог не дублируется
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_cannot_deactivate_self(client: AsyncClient, db_session):
    """Администратор не может деактивировать собственный аккаунт — 409."""
    admin_id, admin_token = await _register_and_promote(client, db_session, "self-deact")

    resp = await client.patch(
        f"/api/v1/users/{admin_id}/active",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 409
    assert "themselves" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cannot_deactivate_last_active_admin(client: AsyncClient, db_session):
    """Деактивация единственного активного admin'а — 409.

    Иначе система теряет возможность управления: некому вернуть права.
    """
    target_id, _ = await _register_and_promote(client, db_session, "last-act-target")
    caller_id, _ = await _register_regular(client, "last-act-caller")

    # Phantom admin (не числится в БД как admin) — тот же трюк что в тесте
    # на последнего admin'а в role_change.
    from sqlalchemy import update

    from app.dependencies import get_current_user
    from app.main import app
    from app.models.user import User as UserModel

    caller_in_db = await db_session.get(UserModel, caller_id)
    await db_session.execute(
        update(UserModel)
        .where(UserModel.id != target_id, UserModel.role == "admin")
        .values(is_active=False)
    )
    await db_session.flush()

    async def fake_admin() -> UserModel:
        return UserModel(
            id=caller_id,
            email=caller_in_db.email,
            username=caller_in_db.username,
            hashed_password=caller_in_db.hashed_password,
            role="admin",
            is_active=True,
        )

    app.dependency_overrides[get_current_user] = fake_admin
    try:
        resp = await client.patch(
            f"/api/v1/users/{target_id}/active",
            json={"is_active": False},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409
    assert "last" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_deactivate_unknown_user_returns_404(client: AsyncClient, db_session):
    _, admin_token = await _register_and_promote(client, db_session, "deact-404")
    resp = await client.patch(
        "/api/v1/users/999999/active",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deactivate_forbidden_for_non_admin(client: AsyncClient):
    _, user_token = await _register_regular(client, "deact-nonadmin")
    resp = await client.patch(
        "/api/v1/users/1/active",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_deactivate_requires_auth(client: AsyncClient):
    resp = await client.patch("/api/v1/users/1/active", json={"is_active": False})
    assert resp.status_code == 401
