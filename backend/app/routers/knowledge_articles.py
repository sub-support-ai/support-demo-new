from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.conversation import Conversation
from app.models.knowledge_article import KnowledgeArticle, KnowledgeArticleFeedback
from app.models.user import User
from app.schemas.knowledge_article import (
    KnowledgeArticleCreate,
    KnowledgeArticleMatch,
    KnowledgeArticleRead,
    KnowledgeArticleUpdate,
    KnowledgeEmbeddingJobRead,
    KnowledgeFeedbackCreate,
    KnowledgeFeedbackRead,
)
from app.services.agents import get_active_agent_for_user
from app.services.audit import log_event
from app.services.knowledge_base import (
    KnowledgeSearchFilters,
    search_knowledge_articles,
    sync_knowledge_article_index,
)
from app.services.knowledge_cache import get_knowledge_cache
from app.services.knowledge_embedding_jobs import (
    enqueue_knowledge_embedding_job,
    notify_knowledge_embedding_jobs_channel,
)
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def _knowledge_visibility(
    query,
    db: AsyncSession,
    current_user: User,
):
    if current_user.role == "admin":
        return query
    if current_user.role == "agent":
        agent = await get_active_agent_for_user(db, current_user)
        if agent is None:
            return query.where(KnowledgeArticle.id == -1)
        return query.where(
            KnowledgeArticle.access_scope.in_(("public", "internal")),
            or_(
                KnowledgeArticle.department == agent.department,
                KnowledgeArticle.department.is_(None),
            ),
        )
    return query.where(KnowledgeArticle.access_scope == "public")


async def _access_scopes_for_user(db: AsyncSession, current_user: User) -> tuple[str, ...]:
    if current_user.role == "admin":
        return ("public", "internal")
    if current_user.role == "agent":
        agent = await get_active_agent_for_user(db, current_user)
        if agent is None:
            return ("public",)
        return ("public", "internal")
    return ("public",)


@router.get("/", response_model=list[KnowledgeArticleRead])
async def list_knowledge_articles(
    department: str | None = Query(default=None),
    request_type: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(KnowledgeArticle)
    query = await _knowledge_visibility(query, db, current_user)
    if active_only:
        query = query.where(KnowledgeArticle.is_active.is_(True))
    if department:
        query = query.where(KnowledgeArticle.department == department)
    if request_type:
        query = query.where(KnowledgeArticle.request_type == request_type)

    query = query.order_by(
        KnowledgeArticle.department.asc(),
        KnowledgeArticle.request_type.asc(),
        KnowledgeArticle.title.asc(),
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/search", response_model=list[KnowledgeArticleMatch])
async def search_knowledge(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=5, ge=1, le=20),
    department: str | None = Query(default=None),
    request_type: str | None = Query(default=None),
    office: str | None = Query(default=None),
    system: str | None = Query(default=None),
    device: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    effective_department = department
    if current_user.role == "agent":
        agent = await get_active_agent_for_user(db, current_user)
        effective_department = "__none__" if agent is None else agent.department

    filters = KnowledgeSearchFilters(
        department=effective_department,
        request_type=request_type,
        office=office,
        system=system,
        device=device,
        access_scopes=await _access_scopes_for_user(db, current_user),
    )
    matches = await search_knowledge_articles(db, q, limit=limit, filters=filters)
    response: list[KnowledgeArticleMatch] = []
    for match in matches:
        article = KnowledgeArticleRead.model_validate(match.article)
        response.append(
            KnowledgeArticleMatch(
                **article.model_dump(),
                score=match.score,
                decision=match.decision,
                chunk_id=match.chunk_id,
                snippet=match.snippet,
                retrieval=match.retrieval,
            )
        )
    return response


@router.post(
    "/",
    response_model=KnowledgeArticleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_article(
    payload: KnowledgeArticleCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    if not payload.title:
        raise HTTPException(status_code=400, detail="Поле 'title' обязательно")

    article = KnowledgeArticle(
        department=payload.department,
        request_type=payload.request_type,
        title=payload.title,
        body=payload.body,
        problem=payload.problem,
        symptoms=payload.symptoms,
        applies_to=payload.applies_to,
        steps=payload.steps,
        when_to_escalate=payload.when_to_escalate,
        required_context=payload.required_context,
        keywords=payload.keywords,
        source_url=payload.source_url,
        owner=payload.owner,
        access_scope=payload.access_scope,
        version=payload.version,
        reviewed_at=payload.reviewed_at,
        expires_at=payload.expires_at,
        is_active=payload.is_active,
    )
    db.add(article)

    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()  # Снимаем блокировку сессии
        raise HTTPException(status_code=400, detail="Ошибка схемы БД: проверьте обязательные поля")

    # ✅ 3. Фиксируем транзакцию (если ваша зависимость get_db не делает commit автоматически)
    await db.commit()
    await db.refresh(article)

    # await db.flush()

    await sync_knowledge_article_index(db, article)
    # Авто-enqueue embedding-job: без него только что созданная статья будет
    # видна только в FTS, а в семантическом поиске не появится до ручного
    # POST /reindex. Embedding-воркер подхватит job, пройдёт по chunks,
    # обновит pgvector. enqueue_knowledge_embedding_job сам делает
    # дедупликацию: если active job на этой статье уже есть — вернёт его.
    await enqueue_knowledge_embedding_job(
        db,
        article_id=article.id,
        requested_by_user_id=admin.id,
    )
    await db.flush()
    await db.refresh(article)

    # Сбрасываем кэш поиска: новая статья должна сразу появляться в выдаче,
    # а не висеть TTL=60c, пока кто-то не дождётся истечения.
    get_knowledge_cache().clear()

    await log_event(
        db,
        action="knowledge_article.create",
        user_id=admin.id,
        target_type="knowledge_article",
        target_id=article.id,
        request=request,
        details={
            "department": article.department,
            "request_type": article.request_type,
        },
    )
    from app.config import get_settings

    background_tasks.add_task(notify_knowledge_embedding_jobs_channel, get_settings().DATABASE_URL)
    return article


@router.post("/reindex", response_model=KnowledgeEmbeddingJobRead)
async def reindex_all_knowledge_articles(
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    job = await enqueue_knowledge_embedding_job(
        db,
        article_id=None,
        requested_by_user_id=admin.id,
    )
    await db.flush()
    await db.refresh(job)

    await log_event(
        db,
        action="knowledge_article.reindex_all",
        user_id=admin.id,
        target_type="knowledge_article",
        target_id=None,
        request=request,
        details={"job_id": job.id, "status": job.status},
    )
    from app.config import get_settings

    background_tasks.add_task(notify_knowledge_embedding_jobs_channel, get_settings().DATABASE_URL)
    return job


@router.post("/{article_id}/reindex", response_model=KnowledgeEmbeddingJobRead)
async def reindex_knowledge_article(
    article_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    article = await db.get(KnowledgeArticle, article_id)
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge article not found",
        )

    await sync_knowledge_article_index(db, article)
    job = await enqueue_knowledge_embedding_job(
        db,
        article_id=article.id,
        requested_by_user_id=admin.id,
    )
    await db.flush()
    await db.refresh(job)

    await log_event(
        db,
        action="knowledge_article.reindex",
        user_id=admin.id,
        target_type="knowledge_article",
        target_id=article.id,
        request=request,
        details={"job_id": job.id, "status": job.status},
    )
    from app.config import get_settings

    background_tasks.add_task(notify_knowledge_embedding_jobs_channel, get_settings().DATABASE_URL)
    return job


@router.patch("/{article_id}", response_model=KnowledgeArticleRead)
async def update_knowledge_article(
    article_id: int,
    payload: KnowledgeArticleUpdate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    article = await db.get(KnowledgeArticle, article_id)
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge article not found",
        )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return article

    for field, value in updates.items():
        setattr(article, field, value)
    if "version" not in updates:
        article.version = (article.version or 1) + 1
    await sync_knowledge_article_index(db, article)
    # Поля, при изменении которых embedding обязательно надо пересчитать.
    # Если правится только access_scope или is_active — текст чанков не
    # меняется, sync_knowledge_article_index не сбросит embedding_model,
    # и worker'у нечего будет делать. Но мы всё равно enqueue'им — сам
    # worker через needs_embedding/_chunk_ids_missing_embeddings поймёт,
    # что обновлять нечего, и закроет job как done(updated=0). Это дешевле,
    # чем городить набор «трогающих текст» полей здесь.
    await enqueue_knowledge_embedding_job(
        db,
        article_id=article.id,
        requested_by_user_id=admin.id,
    )
    await db.flush()
    await db.refresh(article)

    # Сбрасываем кэш — правки статьи (текст, флаги, expires_at) должны
    # отражаться в поиске сразу, а не через TTL.
    get_knowledge_cache().clear()

    await log_event(
        db,
        action="knowledge_article.update",
        user_id=admin.id,
        target_type="knowledge_article",
        target_id=article.id,
        request=request,
        details={"fields": sorted(updates.keys())},
    )
    from app.config import get_settings

    background_tasks.add_task(notify_knowledge_embedding_jobs_channel, get_settings().DATABASE_URL)
    return article


@router.post("/{article_id}/suppress", response_model=KnowledgeArticleRead)
async def suppress_knowledge_article(
    article_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> KnowledgeArticle:
    """Вручную подавляет статью (grade → suppressed).

    Автоматический пересчёт grade НЕ снимает suppressed — только этот endpoint
    или /restore могут это сделать.
    """
    article = await db.get(KnowledgeArticle, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    article.quality_grade = "suppressed"
    article.quality_grade_updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(article)
    return article


@router.post("/{article_id}/restore", response_model=KnowledgeArticleRead)
async def restore_knowledge_article(
    article_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> KnowledgeArticle:
    """Снимает suppressed и пересчитывает grade по текущему фидбеку."""
    from app.services.quality_signals import refresh_article_quality_grade

    article = await db.get(KnowledgeArticle, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Сбрасываем suppressed вручную, чтобы refresh_article_quality_grade не пропустил.
    article.quality_grade = "good"
    await db.flush()
    await refresh_article_quality_grade(article_id, db)
    await db.refresh(article)
    return article


@router.post("/feedback", response_model=KnowledgeFeedbackRead)
async def submit_knowledge_feedback(
    payload: KnowledgeFeedbackCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(KnowledgeArticleFeedback)
        .where(
            KnowledgeArticleFeedback.article_id == payload.article_id,
            KnowledgeArticleFeedback.message_id == payload.message_id,
        )
        .limit(1)
    )
    feedback = result.scalar_one_or_none()
    if feedback is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge feedback target not found",
        )

    conversation = await db.get(Conversation, feedback.conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    if current_user.role != "admin" and conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge feedback target not found",
        )

    article = await db.get(KnowledgeArticle, payload.article_id)
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge article not found",
        )

    previous_feedback = feedback.feedback
    if previous_feedback == payload.feedback:
        return feedback

    _apply_feedback_counter(article, previous_feedback, -1)
    _apply_feedback_counter(article, payload.feedback, 1)
    feedback.feedback = payload.feedback
    await db.flush()
    await db.refresh(feedback)
    return feedback


def _apply_feedback_counter(
    article: KnowledgeArticle,
    feedback: str | None,
    delta: int,
) -> None:
    if feedback == "helped":
        article.helped_count = max(0, article.helped_count + delta)
    elif feedback == "not_helped":
        article.not_helped_count = max(0, article.not_helped_count + delta)
    elif feedback == "not_relevant":
        article.not_relevant_count = max(0, article.not_relevant_count + delta)
