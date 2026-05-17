from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_job import AIJob
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.conversation_ai import generate_ai_message

AI_JOB_QUEUED = "queued"
AI_JOB_RUNNING = "running"
AI_JOB_DONE = "done"
AI_JOB_FAILED = "failed"
ACTIVE_AI_JOB_STATUSES = (AI_JOB_QUEUED, AI_JOB_RUNNING)


async def has_ai_response_after_latest_user(
    db: AsyncSession,
    conversation_id: int,
) -> bool:
    latest_user_result = await db.execute(
        select(Message.id)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "user",
        )
        .order_by(Message.id.desc())
        .limit(1)
    )
    latest_user_id = latest_user_result.scalar_one_or_none()
    if latest_user_id is None:
        return False

    latest_ai_result = await db.execute(
        select(Message.id)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "ai",
        )
        .order_by(Message.id.desc())
        .limit(1)
    )
    latest_ai_id = latest_ai_result.scalar_one_or_none()
    return latest_ai_id is not None and latest_ai_id > latest_user_id


async def finish_ai_job(db: AsyncSession, job: AIJob) -> None:
    now = datetime.now(UTC)
    job.status = AI_JOB_DONE
    job.finished_at = now
    job.error = None
    await db.flush()


async def enqueue_ai_response_job(
    db: AsyncSession,
    conversation_id: int,
) -> AIJob:
    existing = await db.execute(
        select(AIJob)
        .where(
            AIJob.conversation_id == conversation_id,
            AIJob.status.in_(ACTIVE_AI_JOB_STATUSES),
        )
        .order_by(AIJob.id.desc())
        .limit(1)
    )
    job = existing.scalar_one_or_none()
    if job is not None:
        return job

    job = AIJob(
        conversation_id=conversation_id,
        status=AI_JOB_QUEUED,
        attempts=0,
        max_attempts=3,
        run_after=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


async def claim_next_ai_job(db: AsyncSession) -> AIJob | None:
    now = datetime.now(UTC)
    result = await db.execute(
        select(AIJob)
        .where(
            AIJob.status == AI_JOB_QUEUED,
            AIJob.run_after <= now,
        )
        .order_by(AIJob.run_after.asc(), AIJob.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return None

    job.status = AI_JOB_RUNNING
    job.attempts += 1
    job.locked_at = now
    job.started_at = now
    job.error = None
    await db.flush()
    await db.refresh(job)
    return job


async def requeue_stale_ai_jobs(
    db: AsyncSession,
    stale_after_seconds: int,
    limit: int = 50,
) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
    result = await db.execute(
        select(AIJob)
        .where(
            AIJob.status == AI_JOB_RUNNING,
            AIJob.locked_at.is_not(None),
            AIJob.locked_at < cutoff,
        )
        .order_by(AIJob.locked_at.asc(), AIJob.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    jobs = result.scalars().all()
    for job in jobs:
        if job.attempts < job.max_attempts:
            job.status = AI_JOB_QUEUED
            job.run_after = datetime.now(UTC)
            job.locked_at = None
            job.started_at = None
            job.error = "Job was requeued after stale running lock"
            # ПСЕВДО-СТРИМИНГ: сбрасываем зависшую стадию — при повторной
            # попытке generate_ai_message выставит её заново с "thinking".
            conversation = await db.get(Conversation, job.conversation_id)
            if conversation is not None:
                conversation.ai_stage = None
        else:
            job.status = AI_JOB_FAILED
            job.finished_at = datetime.now(UTC)
            job.error = "Job failed after stale running lock"
            conversation = await db.get(Conversation, job.conversation_id)
            if conversation is not None and conversation.status == "ai_processing":
                conversation.status = "active"
            # ПСЕВДО-СТРИМИНГ: финальный провал — сбрасываем стадию.
            if conversation is not None:
                conversation.ai_stage = None
    await db.flush()
    return len(jobs)


async def _save_failure_message(db: AsyncSession, conversation_id: int) -> None:
    """Создаёт AI-сообщение с fallback-текстом когда джоба исчерпала все попытки.

    Без этого пользователь видит бесконечный спиннер — conversation.status
    становится "active", но AI-сообщения нет.
    """
    if await has_ai_response_after_latest_user(db, conversation_id):
        return
    from app.models.message import Message

    db.add(
        Message(
            conversation_id=conversation_id,
            role="ai",
            content=(
                "Не удалось подготовить автоматический ответ. "
                "Создайте запрос в поддержку — специалист поможет разобраться."
            ),
            ai_confidence=0.0,
            ai_escalate=True,
            requires_escalation=True,
        )
    )
    await db.flush()


async def process_ai_job(db: AsyncSession, job: AIJob) -> None:
    if await has_ai_response_after_latest_user(db, job.conversation_id):
        conversation = await db.get(Conversation, job.conversation_id)
        if conversation is not None and conversation.status == "ai_processing":
            conversation.status = "active"
            conversation.ai_stage = None
        await finish_ai_job(db, job)
        return

    try:
        await generate_ai_message(db, job.conversation_id)
    except Exception as exc:
        await fail_ai_job(db, job, exc)
        if job.status == AI_JOB_FAILED:
            await _save_failure_message(db, job.conversation_id)
        return

    await finish_ai_job(db, job)


async def notify_ai_jobs_channel(database_url: str) -> None:
    """Послать pg_notify после коммита новой джобы.

    Вызывается как BackgroundTask после db.commit() в роутере, чтобы
    ai_worker проснулся немедленно, не ожидая следующего таймаута.
    No-op для SQLite (тесты) и при недоступном asyncpg.
    """
    from app.pg_notify import notify

    await notify(database_url, "ai_jobs")


async def fail_ai_job(db: AsyncSession, job: AIJob, exc: Exception) -> None:
    now = datetime.now(UTC)
    job.error = str(exc)[:2000]
    if job.attempts < job.max_attempts:
        job.status = AI_JOB_QUEUED
        job.run_after = now + timedelta(seconds=min(60, 2**job.attempts * 5))
    else:
        job.status = AI_JOB_FAILED
        job.finished_at = now
        conversation = await db.get(Conversation, job.conversation_id)
        if conversation is not None and conversation.status == "ai_processing":
            conversation.status = "active"
        # ПСЕВДО-СТРИМИНГ: сбрасываем стадию при окончательном провале
        # джобы, чтобы UI не завис с "Формирую ответ..." навсегда.
        if conversation is not None:
            conversation.ai_stage = None
    await db.flush()
