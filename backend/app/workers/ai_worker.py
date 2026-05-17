import asyncio
import logging
import os

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.metrics import record_job_duration
from app.services.ai_jobs import (
    AI_JOB_FAILED,
    _save_failure_message,
    claim_next_ai_job,
    fail_ai_job,
    process_ai_job,
    requeue_stale_ai_jobs,
)
from app.workers.base import BaseWorker

logger = logging.getLogger(__name__)

# JOB_TIMEOUT_SECONDS — потолок времени на одну AI-джобу.
# При превышении джоба помечается failed и конверсация возвращается в active.
JOB_TIMEOUT_SECONDS = float(os.getenv("AI_WORKER_JOB_TIMEOUT_SECONDS", "240"))


class JobTimeoutError(TimeoutError):
    pass


class AIWorker(BaseWorker):
    NOTIFY_CHANNEL = "ai_jobs"
    # Раньше: POLL_INTERVAL_SECONDS=0.2 → 5 холостых SELECT/сек.
    # Теперь: воркер спит до NOTIFY; этот таймаут — только fallback на случай
    # если NOTIFY потерялось (обрыв соединения, restart, редкий edge case).
    NOTIFY_TIMEOUT_SECONDS = float(os.getenv("AI_WORKER_NOTIFY_TIMEOUT_SECONDS", "2.0"))
    WORKER_NAME = "AI worker"

    async def run_once(self) -> bool:
        settings = get_settings()
        async with AsyncSessionLocal() as db:
            await requeue_stale_ai_jobs(db, settings.AI_WORKER_STALE_RUNNING_SECONDS)
            job = await claim_next_ai_job(db)
            if job is None:
                await db.commit()
                return False
            try:
                with record_job_duration("ai"):
                    await asyncio.wait_for(
                        process_ai_job(db, job),
                        timeout=JOB_TIMEOUT_SECONDS,
                    )
            except TimeoutError:
                await fail_ai_job(
                    db,
                    job,
                    JobTimeoutError(f"AI job exceeded {JOB_TIMEOUT_SECONDS:.0f}s timeout"),
                )
                if job.status == AI_JOB_FAILED:
                    await _save_failure_message(db, job.conversation_id)
            await db.commit()
        return True


# ── Обратная совместимость ────────────────────────────────────────────────────
# docker-compose запускает воркер через `python -m app.workers.ai_worker`,
# поэтому модуль-уровневые функции сохраняем как тонкие обёртки.

_worker = AIWorker()


async def run_once() -> bool:
    return await _worker.run_once()


async def run_forever() -> None:
    await _worker.run_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
