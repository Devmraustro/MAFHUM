"""Phase 5 tutoring endpoints.

Every route is authenticated and every lookup is owner-scoped inside the service,
so isolation does not depend on anything the client sends. A topic, session,
question, card or plan item belonging to another user is a 404.

No route here presents generated content as verified. ``submit_answer`` reports
what a lexical comparison against the reference passage established and says
explicitly that it did not grade meaning.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from mafhum_api.api.deps import Context, CsrfGuard, CurrentUser, DbSession
from mafhum_api.db.models import Flashcard, StudyQuestion, StudySession, StudyTopic
from mafhum_api.services.study.service import StudyError

router = APIRouter(prefix="/study", tags=["study"])


# -- request bodies -----------------------------------------------------


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    language: Literal["ar", "fr"] = "ar"


class MaterialAttach(BaseModel):
    document_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class GenerateRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=20)


class SubmitAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


class FlashcardCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=4000)
    segment_id: uuid.UUID | None = None


class ReviewRequest(BaseModel):
    rating: int = Field(ge=0, le=5)


class PlanItemCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=1000)
    scheduled_for: str = Field(min_length=10, max_length=10)


class PlanStateRequest(BaseModel):
    state: Literal["not_started", "in_progress", "completed", "blocked"]


# -- serialization ------------------------------------------------------


def _topic(topic: StudyTopic) -> dict[str, object]:
    return {
        "id": str(topic.id),
        "title": topic.title,
        "description": topic.description,
        "language": topic.language,
        "created_at": topic.created_at.isoformat(),
    }


def _session(session: StudySession) -> dict[str, object]:
    return {
        "id": str(session.id),
        "question": session.question,
        "answer": session.answer,
        "status": session.status,
        "evidence_sufficient": session.evidence_sufficient,
        "coverage": session.coverage,
        "citations": session.citations,
        "created_at": session.created_at.isoformat(),
    }


def _question(question: StudyQuestion) -> dict[str, object]:
    return {
        "id": str(question.id),
        "prompt": question.prompt,
        "difficulty": question.difficulty,
        "answer_submitted": question.answer_submitted,
        "submitted_answer": question.submitted_answer,
        "feedback": question.feedback,
        "source_document_id": (
            str(question.source_document_id) if question.source_document_id else None
        ),
        "source_page_number": question.source_page_number,
        "source_ordinal": question.source_ordinal,
        "source_available": question.source_segment_id is not None,
    }


def _card(card: Flashcard) -> dict[str, object]:
    return {
        "id": str(card.id),
        "prompt": card.prompt,
        "answer": card.answer,
        "source_title": card.source_title,
        "source_page_number": card.source_page_number,
        "source_ordinal": card.source_ordinal,
        "source_available": card.source_available,
        "due_at": card.due_at.isoformat(),
        "interval_days": card.interval_days,
        "repetitions": card.repetitions,
        "review_count": card.review_count,
    }


def _guard(action: object) -> None:
    """Convert a service error into the status it asked for."""
    if isinstance(action, StudyError):
        raise HTTPException(status_code=action.status_code, detail=action.message)


# -- topics -------------------------------------------------------------


@router.get("/topics", status_code=status.HTTP_200_OK)
def list_topics(user: CurrentUser, context: Context, db: DbSession) -> dict[str, object]:
    topics = context.study.list_topics(db, owner_id=user.id)
    return {"topics": [_topic(t) for t in topics]}


@router.post("/topics", status_code=status.HTTP_201_CREATED, dependencies=[CsrfGuard])
def create_topic(
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: TopicCreate,
) -> dict[str, object]:
    try:
        topic = context.study.create_topic(
            db,
            owner_id=user.id,
            title=payload.title,
            description=payload.description,
            language=payload.language,
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return _topic(topic)


@router.get("/topics/{topic_id}", status_code=status.HTTP_200_OK)
def get_topic(
    topic_id: uuid.UUID, user: CurrentUser, context: Context, db: DbSession
) -> dict[str, object]:
    try:
        topic = context.study.get_topic(db, owner_id=user.id, topic_id=topic_id)
        materials = context.study.list_materials(db, owner_id=user.id, topic_id=topic_id)
        plan = context.study.plan_summary(db, owner_id=user.id, topic_id=topic_id)
    except StudyError as exc:
        _guard(exc)
        raise
    return {**_topic(topic), "materials": materials, "plan": plan}


@router.post(
    "/topics/{topic_id}/materials", status_code=status.HTTP_201_CREATED, dependencies=[CsrfGuard]
)
def attach_material(
    topic_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: MaterialAttach,
) -> dict[str, object]:
    try:
        material = context.study.attach_material(
            db,
            owner_id=user.id,
            topic_id=topic_id,
            document_id=payload.document_id,
            source_id=payload.source_id,
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return {
        "id": str(material.id),
        "document_id": str(material.document_id) if material.document_id else None,
        "source_id": str(material.source_id) if material.source_id else None,
    }


@router.get("/topics/{topic_id}/materials", status_code=status.HTTP_200_OK)
def list_materials(
    topic_id: uuid.UUID, user: CurrentUser, context: Context, db: DbSession
) -> dict[str, object]:
    try:
        return {"materials": context.study.list_materials(db, owner_id=user.id, topic_id=topic_id)}
    except StudyError as exc:
        _guard(exc)
        raise


# -- grounded sessions --------------------------------------------------


@router.post(
    "/topics/{topic_id}/ask", status_code=status.HTTP_201_CREATED, dependencies=[CsrfGuard]
)
def ask(
    topic_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: AskRequest,
) -> dict[str, object]:
    try:
        session = context.study.ask(
            db, owner_id=user.id, topic_id=topic_id, question=payload.question
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return _session(session)


@router.get("/topics/{topic_id}/sessions", status_code=status.HTTP_200_OK)
def list_sessions(
    topic_id: uuid.UUID, user: CurrentUser, context: Context, db: DbSession
) -> dict[str, object]:
    try:
        sessions = context.study.list_sessions(db, owner_id=user.id, topic_id=topic_id)
    except StudyError as exc:
        _guard(exc)
        raise
    return {"sessions": [_session(s) for s in sessions]}


# -- questions ----------------------------------------------------------


@router.post(
    "/topics/{topic_id}/questions", status_code=status.HTTP_201_CREATED, dependencies=[CsrfGuard]
)
def generate_questions(
    topic_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: GenerateRequest,
) -> dict[str, object]:
    try:
        questions = context.study.generate_questions(
            db, owner_id=user.id, topic_id=topic_id, count=payload.count
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return {"questions": [_question(q) for q in questions]}


@router.get("/topics/{topic_id}/questions", status_code=status.HTTP_200_OK)
def list_questions(
    topic_id: uuid.UUID, user: CurrentUser, context: Context, db: DbSession
) -> dict[str, object]:
    try:
        questions = context.study.list_questions(db, owner_id=user.id, topic_id=topic_id)
    except StudyError as exc:
        _guard(exc)
        raise
    return {"questions": [_question(q) for q in questions]}


@router.post("/topics/{topic_id}/questions/{question_id}/answer", status_code=status.HTTP_200_OK)
def submit_answer(
    topic_id: uuid.UUID,
    question_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: SubmitAnswerRequest,
) -> dict[str, object]:
    try:
        question = context.study.submit_answer(
            db,
            owner_id=user.id,
            topic_id=topic_id,
            question_id=question_id,
            answer=payload.answer,
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return _question(question)


# -- flashcards ---------------------------------------------------------


@router.post(
    "/topics/{topic_id}/flashcards", status_code=status.HTTP_201_CREATED, dependencies=[CsrfGuard]
)
def create_flashcard(
    topic_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: FlashcardCreate,
) -> dict[str, object]:
    try:
        card = context.study.create_flashcard(
            db,
            owner_id=user.id,
            topic_id=topic_id,
            prompt=payload.prompt,
            answer=payload.answer,
            segment_id=payload.segment_id,
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return _card(card)


@router.get("/topics/{topic_id}/flashcards/due", status_code=status.HTTP_200_OK)
def due_flashcards(
    topic_id: uuid.UUID, user: CurrentUser, context: Context, db: DbSession
) -> dict[str, object]:
    try:
        cards = context.study.list_due_cards(db, owner_id=user.id, topic_id=topic_id)
    except StudyError as exc:
        _guard(exc)
        raise
    return {"cards": [_card(c) for c in cards]}


@router.post(
    "/topics/{topic_id}/flashcards/{flashcard_id}/review",
    status_code=status.HTTP_200_OK,
    dependencies=[CsrfGuard],
)
def review_flashcard(
    topic_id: uuid.UUID,
    flashcard_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: ReviewRequest,
) -> dict[str, object]:
    try:
        card = context.study.review_card(
            db,
            owner_id=user.id,
            topic_id=topic_id,
            flashcard_id=flashcard_id,
            rating=payload.rating,
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return _card(card)


# -- study plans --------------------------------------------------------


@router.post(
    "/topics/{topic_id}/plan", status_code=status.HTTP_201_CREATED, dependencies=[CsrfGuard]
)
def add_plan_item(
    topic_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: PlanItemCreate,
) -> dict[str, object]:
    try:
        item = context.study.add_plan_item(
            db,
            owner_id=user.id,
            topic_id=topic_id,
            objective=payload.objective,
            scheduled_for=payload.scheduled_for,
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return {"id": str(item.id), "objective": item.objective, "state": item.state}


@router.patch(
    "/topics/{topic_id}/plan/{item_id}", status_code=status.HTTP_200_OK, dependencies=[CsrfGuard]
)
def set_plan_state(
    topic_id: uuid.UUID,
    item_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
    payload: PlanStateRequest,
) -> dict[str, object]:
    try:
        item = context.study.set_plan_state(
            db, owner_id=user.id, topic_id=topic_id, item_id=item_id, state=payload.state
        )
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return {
        "id": str(item.id),
        "state": item.state,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


@router.get("/topics/{topic_id}/plan", status_code=status.HTTP_200_OK)
def plan_summary(
    topic_id: uuid.UUID, user: CurrentUser, context: Context, db: DbSession
) -> dict[str, object]:
    try:
        return context.study.plan_summary(db, owner_id=user.id, topic_id=topic_id)
    except StudyError as exc:
        _guard(exc)
        raise


@router.delete(
    "/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[CsrfGuard]
)
def delete_topic(
    topic_id: uuid.UUID,
    user: CurrentUser,
    context: Context,
    db: DbSession,
) -> Response:
    try:
        topic = context.study.get_topic(db, owner_id=user.id, topic_id=topic_id)
        db.delete(topic)
        db.commit()
    except StudyError as exc:
        _guard(exc)
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)
