# Пароли хэшируем как SHA-256(hex) → bcrypt.
# См. _prepare_password ниже — почему не bcrypt напрямую и не SHA-256 в одиночку.
#
# Хэш необратим: зная хэш, восстановить пароль невозможно.
# При входе — verify_password() сравнивает введённый пароль с хэшем,
# не расшифровывая.


import hashlib
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import jwt

from app.config import get_settings

settings = get_settings()


# ── Пароли ────────────────────────────────────────────────────────────────────


def _prepare_password(password: str) -> bytes:
    """Пре-хешируем пароль через SHA-256 → hex перед передачей в bcrypt.

    Зачем такая цепочка, а не bcrypt(password) напрямую:
      bcrypt принимает МАКСИМУМ 72 байта ключа. Всё, что длиннее, молча
      отбрасывается — любые пароли, совпадающие по первым 72 байтам,
      считаются эквивалентными. Пример коллизии:
        "a"*72 + "X"   и   "a"*72 + "Y"   — для bcrypt один пароль.
      Особенно коварно с UTF-8: 40 кириллических символов = 80 байт → cap.

    Зачем SHA-256 + bcrypt, а не просто SHA-256:
      SHA-256 быстрый — миллионы хэшей/сек на GPU. Утёкший хэш брутфорсится
      за секунды на словаре. bcrypt намеренно МЕДЛЕННЫЙ (cost factor) —
      это и есть защита от offline-атаки. SHA-256 только нормализует длину,
      стоимость даёт bcrypt.

    Зачем hex, а не сырые 32 байта digest:
      Исторически часть bcrypt-реализаций NUL-terminate'ила вход. Если в
      digest попадает байт 0x00 — обрезание. Hex: всегда 64 ASCII-байта,
      никаких NUL, одинаково во всех имплементациях. Django использует тот
      же приём (django.contrib.auth.hashers.BCryptSHA256PasswordHasher).
    """
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return digest.encode("ascii")  # 64 байта ASCII — безопасно для bcrypt


def hash_password(password: str) -> str:
    """Превратить пароль в bcrypt-хэш для хранения в БД."""
    salt = bcrypt.gensalt(rounds=settings.PASSWORD_BCRYPT_ROUNDS)
    return bcrypt.hashpw(_prepare_password(password), salt).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверить пароль при входе. True если совпадает."""
    return bcrypt.checkpw(_prepare_password(plain_password), hashed_password.encode())


# ── JWT токены ────────────────────────────────────────────────────────────────


def create_access_token(user_id: int, role: str) -> str:
    """
    Создать JWT токен для пользователя.
    Внутри токена зашиты: id пользователя, его роль, время истечения.
    Токен подписан секретным ключом — подделать нельзя.
    """
    expire = datetime.now(UTC) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),  # sub = subject, стандартное поле JWT
        "role": role,
        "exp": expire,  # exp = expiration, когда токен истекает
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """
    Расшифровать токен и вернуть данные внутри.
    Если токен неверный или истёк — выбросит JWTError.
    """
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithms=[settings.JWT_ALGORITHM],
    )
