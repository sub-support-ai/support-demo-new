"""
Абстрактный базовый класс для всех async job-воркеров.

Решает проблему copy-paste: ai_worker, knowledge_embedding_worker и
sla_worker содержали идентичный boilerplate (_stop_event, signal handlers,
run_forever-loop). BaseWorker инкапсулирует его в одном месте.

Использование:

    class MyWorker(BaseWorker):
        NOTIFY_CHANNEL = "my_jobs"          # "" — отключает pg_notify
        NOTIFY_TIMEOUT_SECONDS = 2.0        # fallback-поллинг
        WORKER_NAME = "My worker"

        async def run_once(self) -> bool:
            # Вернуть True если джоб был обработан, False если очередь пуста.
            ...

    worker = MyWorker()
    asyncio.run(worker.run_forever())

Обратная совместимость:
    Модуль-уровневые run_once() / run_forever() в каждом воркере сохраняются
    как тонкие обёртки вокруг экземпляра воркера, потому что docker-compose
    запускает воркеры через `python -m app.workers.ai_worker`.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from abc import ABC, abstractmethod
from contextlib import suppress

logger = logging.getLogger(__name__)


class BaseWorker(ABC):
    # ── Класс-переменные, которые переопределяют подклассы ───────────────────

    # Название канала pg_notify. Пустая строка — LISTEN не используется,
    # воркер работает в режиме чистого таймаутного поллинга.
    NOTIFY_CHANNEL: str = ""

    # Максимум секунд ожидания уведомления (или timeout между итерациями).
    # Для event-driven воркеров (ai, embedding) — 2 с как fallback.
    # Для timer-based (sla) — равно интервалу тика (30 с).
    NOTIFY_TIMEOUT_SECONDS: float = 2.0

    # Имя для логов
    WORKER_NAME: str = "worker"

    # ── Инициализация ────────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._stop_event: asyncio.Event = asyncio.Event()

    # ── Абстрактный интерфейс ────────────────────────────────────────────────

    @abstractmethod
    async def run_once(self) -> bool:
        """Обработать одну джобу.

        Возвращает True если джоба была обработана, False если очередь пуста.

        ВАЖНО: не должен пробрасывать исключения наружу — только логировать.
        _run_once_safe() оборачивает вызов дополнительной защитой, но
        хорошим тоном считается обрабатывать ошибки внутри.
        """

    # ── Сигналы ──────────────────────────────────────────────────────────────

    def _request_shutdown(self) -> None:
        logger.info("%s: shutdown requested", self.WORKER_NAME)
        self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                # Windows не поддерживает loop.add_signal_handler
                signal.signal(sig, lambda _s, _f: self._request_shutdown())

    # ── Основной цикл ────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Запустить цикл воркера. Блокирует до SIGINT/SIGTERM или stop()."""
        self._install_signal_handlers()

        from app.config import get_settings
        from app.pg_notify import listen_for_notifications

        settings = get_settings()

        async with listen_for_notifications(
            settings.DATABASE_URL,
            self.NOTIFY_CHANNEL,
            self._stop_event,
            max_wait_seconds=self.NOTIFY_TIMEOUT_SECONDS,
        ) as wake_queue:
            logger.info("%s: started", self.WORKER_NAME)
            while not self._stop_event.is_set():
                processed = await self._run_once_safe()

                # Если только что обработали джобу — сразу в следующую итерацию:
                # в очереди могут ещё быть задачи. Ждём только если очередь была
                # пустой — тогда спим до уведомления или таймаута.
                if not processed:
                    stop_task = asyncio.ensure_future(self._stop_event.wait())
                    queue_task = asyncio.ensure_future(wake_queue.get())
                    _, pending = await asyncio.wait(
                        {stop_task, queue_task},
                        timeout=self.NOTIFY_TIMEOUT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        with suppress(asyncio.CancelledError):
                            await t

            logger.info("%s: stopped", self.WORKER_NAME)

    async def _run_once_safe(self) -> bool:
        """Вызывает run_once() с защитой от неожиданных исключений."""
        try:
            return await self.run_once()
        except Exception:
            logger.exception("%s: unexpected error in run_once", self.WORKER_NAME)
            return False

    def stop(self) -> None:
        """Программная остановка воркера (полезна в тестах)."""
        self._stop_event.set()
