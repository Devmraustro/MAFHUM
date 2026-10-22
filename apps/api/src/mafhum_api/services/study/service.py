"""Phase 5 tutoring: study workspaces, questions, flashcards and plans.

Design rules that hold across every method here
-----------------------------------------------
* **Ownership is enforced in the query, not in the view.** Every lookup filters
  on ``owner_id``. A topic belonging to another user is a 404, never a 403, so
  that probing for a topic id cannot confirm it exists.
* **Nothing is grounded without provenance.** Sessions store their citations,
  questions store the segment they were built from, flashcards store the locator
  of their answer.
* **A deleted source never becomes a lie.** Material links, question segments and
  flashcard locators use ``SET NULL`` rather than ``CASCADE``; the derived
  artifact survives and reports the reference as unavailable.
* **Reuse, do not parallel.** Grounding goes through
  ``services.research.grounding`` over real ``DocumentTextSegment`` rows. There is
  no second retrieval or scoring path.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mafhum_api.db.models import (
    Document,
    DocumentTextSegment,
    Flashcard,
    FlashcardReview,
    SourceRecord,
    StudyPlanItem,
    StudyQuestion,
    StudySession,
    StudyTopic,
    StudyTopicMaterial,
)
from mafhum_api.services.research.grounding import Evidence, ground_answer
from mafhum_api.services.study.scheduling import (
    INITIAL_EASE_FACTOR,
    MAX_RATING,
    MIN_RATING,
    schedule,
)

logger = logging.getLogger(__name__)

PLAN_STATES = ("not_started", "in_progress", "completed", "blocked")
DIFFICULTIES = ("recall", "comprehension", "application")


class StudyError(Exception):
    """A study operation that cannot proceed."""

    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class NotFoundError(StudyError):
    def __init__(self, what: str = "العنصر") -> None:
        super().__init__(f"{what} غير موجود.", code="not_found", status_code=404)


class StudyService:
    """Owns every tutoring operation."""

    # -- topics ---------------------------------------------------------

    def create_topic(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        title: str,
        description: str | None,
        language: str,
    ) -> StudyTopic:
        if not title or not title.strip():
            raise StudyError("العنوان مطلوب.", code="title_required")
        if language not in ("ar", "fr"):
            raise StudyError("اللغة غير مدعومة.", code="unsupported_language")
        topic = StudyTopic(
            id=uuid.uuid4(),
            owner_id=owner_id,
            title=title.strip()[:300],
            description=(description or "").strip() or None,
            language=language,
        )
        db.add(topic)
        db.flush()
        return topic

    def list_topics(self, db: Session, *, owner_id: uuid.UUID) -> list[StudyTopic]:
        return list(
            db.scalars(
                select(StudyTopic)
                .where(StudyTopic.owner_id == owner_id, StudyTopic.archived.is_(False))
                .order_by(StudyTopic.created_at.desc())
            )
        )

    def get_topic(self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID) -> StudyTopic:
        """Fetch a topic, or raise NotFound.

        Filtering on owner_id in the query is the isolation boundary. Someone
        else's topic is indistinguishable from one that does not exist.
        """
        topic = db.scalar(
            select(StudyTopic).where(StudyTopic.id == topic_id, StudyTopic.owner_id == owner_id)
        )
        if topic is None:
            raise NotFoundError("الموضوع")
        return topic

    # -- materials ------------------------------------------------------

    def attach_material(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        topic_id: uuid.UUID,
        document_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
    ) -> StudyTopicMaterial:
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        if (document_id is None) == (source_id is None):
            raise StudyError("يجب تحديد مستند أو مصدر واحد.", code="exactly_one_material_target")

        # Ownership of the target is checked here, not trusted from the request.
        # Attaching someone else's document would expose its text through the
        # topic's grounded answers.
        if document_id is not None:
            document = db.scalar(
                select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
            )
            if document is None:
                raise NotFoundError("المستند")
        else:
            source = db.scalar(
                select(SourceRecord).where(
                    SourceRecord.id == source_id, SourceRecord.owner_id == owner_id
                )
            )
            if source is None:
                raise NotFoundError("المصدر")

        existing = db.scalar(
            select(StudyTopicMaterial).where(
                StudyTopicMaterial.topic_id == topic.id,
                StudyTopicMaterial.document_id == document_id,
                StudyTopicMaterial.source_id == source_id,
            )
        )
        if existing is not None:
            return existing

        material = StudyTopicMaterial(
            id=uuid.uuid4(),
            topic_id=topic.id,
            document_id=document_id,
            source_id=source_id,
        )
        db.add(material)
        db.flush()
        return material

    def list_materials(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID
    ) -> list[dict[str, object]]:
        """List a topic's material, marking references whose target is gone."""
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        materials = list(
            db.scalars(select(StudyTopicMaterial).where(StudyTopicMaterial.topic_id == topic.id))
        )
        result: list[dict[str, object]] = []
        for material in materials:
            title: str | None = None
            available = False
            if material.document_id is not None:
                document = db.get(Document, material.document_id)
                available = document is not None
                title = document.display_filename if document else None
            elif material.source_id is not None:
                source = db.get(SourceRecord, material.source_id)
                available = source is not None
                title = source.title if source else None
            result.append(
                {
                    "id": str(material.id),
                    "document_id": str(material.document_id) if material.document_id else None,
                    "source_id": str(material.source_id) if material.source_id else None,
                    "title": title,
                    "available": available,
                }
            )
        return result

    # -- evidence from a topic's own material ---------------------------

    def _evidence_for_topic(
        self, db: Session, *, topic_id: uuid.UUID, owner_id: uuid.UUID, limit: int = 200
    ) -> list[Evidence]:
        """Segments from the documents attached to this topic, and no others.

        The owner filter is repeated here on purpose: material links are created
        under an ownership check, but re-checking at read time means a link
        created before an ownership rule changed cannot leak another user's text.
        """
        materials = list(
            db.scalars(
                select(StudyTopicMaterial.document_id).where(
                    StudyTopicMaterial.topic_id == topic_id,
                    StudyTopicMaterial.document_id.is_not(None),
                )
            )
        )
        if not materials:
            return []
        rows = db.execute(
            select(DocumentTextSegment, Document.display_filename)
            .join(Document, Document.id == DocumentTextSegment.document_id)
            .where(
                DocumentTextSegment.document_id.in_(materials),
                Document.owner_id == owner_id,
            )
            .order_by(DocumentTextSegment.document_id, DocumentTextSegment.ordinal)
            .limit(limit)
        ).all()
        return [
            Evidence(
                kind="document",
                text=segment.text,
                document_id=segment.document_id,
                title=title,
                page_number=segment.page_number,
                ordinal=segment.ordinal,
            )
            for segment, title in rows
        ]

    # -- study sessions -------------------------------------------------

    def ask(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID, question: str
    ) -> StudySession:
        """Answer a question strictly from the topic's attached material."""
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        if not question or not question.strip():
            raise StudyError("السؤال مطلوب.", code="question_required")

        evidence = self._evidence_for_topic(db, topic_id=topic.id, owner_id=owner_id)
        answer = ground_answer(question.strip(), evidence)

        session = StudySession(
            id=uuid.uuid4(),
            topic_id=topic.id,
            question=question.strip(),
            answer=answer.explanation or None,
            status=answer.status,
            evidence_sufficient=answer.evidence_sufficient,
            coverage=answer.coverage,
            citations=[
                {
                    "kind": citation.kind,
                    "quote": citation.quote,
                    "locator": citation.locator,
                    "document_id": str(citation.document_id) if citation.document_id else None,
                    "title": citation.title,
                    "page_number": citation.page_number,
                    "ordinal": citation.ordinal,
                    "score": citation.score,
                }
                for citation in answer.citations
            ],
        )
        db.add(session)
        db.flush()
        return session

    def list_sessions(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID
    ) -> list[StudySession]:
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        return list(
            db.scalars(
                select(StudySession)
                .where(StudySession.topic_id == topic.id)
                .order_by(StudySession.created_at.desc())
            )
        )

    # -- questions ------------------------------------------------------

    def generate_questions(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID, count: int = 5
    ) -> list[StudyQuestion]:
        """Build questions from the topic's material, deterministically.

        No language model is involved. Each question is derived from one segment
        by cloze-deleting its longest content phrase, so the reference answer is
        by construction a verbatim span of that segment. That is what makes the
        grading in ``submit_answer`` checkable rather than a matter of opinion.
        """
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        if not 1 <= count <= 20:
            raise StudyError("عدد الأسئلة يجب أن يكون بين 1 و 20.", code="count_out_of_range")

        evidence = self._evidence_for_topic(db, topic_id=topic.id, owner_id=owner_id)
        created: list[StudyQuestion] = []
        for index, item in enumerate(evidence[:count]):
            words = [w for w in item.text.split() if len(w) > 4]
            if not words:
                continue
            longest = max(words, key=len)
            prompt = item.text.replace(longest, "………", 1)
            question = StudyQuestion(
                id=uuid.uuid4(),
                topic_id=topic.id,
                prompt=f"أكمل: {prompt}",
                difficulty=DIFFICULTIES[index % len(DIFFICULTIES)],
                reference_answer=longest,
                source_document_id=item.document_id,
                source_page_number=item.page_number,
                source_ordinal=item.ordinal,
            )
            # Resolve the segment row so the FK records real provenance.
            segment = db.scalar(
                select(DocumentTextSegment).where(
                    DocumentTextSegment.document_id == item.document_id,
                    DocumentTextSegment.ordinal == item.ordinal,
                )
            )
            question.source_segment_id = segment.id if segment else None
            db.add(question)
            created.append(question)
        if not created:
            raise StudyError(
                "لا توجد مادة كافية لتوليد الأسئلة.",
                code="insufficient_material",
                status_code=409,
            )
        db.flush()
        return created

    def list_questions(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID
    ) -> list[StudyQuestion]:
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        return list(
            db.scalars(
                select(StudyQuestion)
                .where(StudyQuestion.topic_id == topic.id)
                .order_by(StudyQuestion.created_at)
            )
        )

    def submit_answer(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        topic_id: uuid.UUID,
        question_id: uuid.UUID,
        answer: str,
    ) -> StudyQuestion:
        """Grade a submission against the reference passage, not against opinion.

        The feedback separates what the comparison can establish from what it
        cannot. Overlap is lexical, so a paraphrase that is factually correct
        scores as unsupported — that is stated in the feedback rather than hidden
        behind a mark.
        """
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        question = db.scalar(
            select(StudyQuestion).where(
                StudyQuestion.id == question_id, StudyQuestion.topic_id == topic.id
            )
        )
        if question is None:
            raise NotFoundError("السؤال")
        if not answer or not answer.strip():
            raise StudyError("الإجابة مطلوبة.", code="answer_required")

        submitted = answer.strip()
        reference = question.reference_answer.strip()

        # The source passage may have been deleted since the question was made.
        segment = (
            db.get(DocumentTextSegment, question.source_segment_id)
            if question.source_segment_id
            else None
        )

        normalized_submitted = _normalize_for_comparison(submitted)
        normalized_reference = _normalize_for_comparison(reference)
        supported = normalized_reference in normalized_submitted

        question.answer_submitted = True
        question.submitted_answer = submitted
        question.answered_at = datetime.now(UTC)
        question.feedback = {
            # Lexical containment in the reference passage. Nothing more.
            "supported_by_source": supported,
            "reference_excerpt": reference,
            "source_available": segment is not None,
            "verdict": ("supported" if supported else "not_established_by_lexical_comparison"),
            "explanation": (
                "تطابقت إجابتك مع النص المرجعي حرفيًا."
                if supported
                else "لم يُعثر على نص المرجع حرفيًا في إجابتك. قد تكون الإجابة صحيحة "
                "بمعناها، لكن هذه المقارنة لفظية ولا تؤكد ذلك."
            ),
            # Explicitly not a claim of correctness.
            "graded_semantically": False,
        }
        db.flush()
        return question

    # -- flashcards -----------------------------------------------------

    def create_flashcard(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        topic_id: uuid.UUID,
        prompt: str,
        answer: str,
        segment_id: uuid.UUID | None = None,
    ) -> Flashcard:
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        if not prompt.strip() or not answer.strip():
            raise StudyError("البطاقة تحتاج سؤالًا وجوابًا.", code="flashcard_incomplete")

        document_id: uuid.UUID | None = None
        title: str | None = None
        page: int | None = None
        ordinal: int | None = None
        if segment_id is not None:
            segment = db.scalar(
                select(DocumentTextSegment)
                .join(Document, Document.id == DocumentTextSegment.document_id)
                .where(DocumentTextSegment.id == segment_id, Document.owner_id == owner_id)
            )
            if segment is None:
                raise NotFoundError("المقطع")
            document_id = segment.document_id
            page = segment.page_number
            ordinal = segment.ordinal
            title = db.scalar(
                select(Document.display_filename).where(Document.id == segment.document_id)
            )

        now = datetime.now(UTC)
        card = Flashcard(
            id=uuid.uuid4(),
            topic_id=topic.id,
            prompt=prompt.strip(),
            answer=answer.strip(),
            source_document_id=document_id,
            source_title=title,
            source_page_number=page,
            source_ordinal=ordinal,
            source_available=document_id is not None,
            ease_factor=INITIAL_EASE_FACTOR,
            interval_days=0,
            repetitions=0,
            due_at=now,
        )
        db.add(card)
        db.flush()
        return card

    def list_due_cards(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID, now: datetime | None = None
    ) -> list[Flashcard]:
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        moment = now or datetime.now(UTC)
        return list(
            db.scalars(
                select(Flashcard)
                .where(Flashcard.topic_id == topic.id, Flashcard.due_at <= moment)
                .order_by(Flashcard.due_at)
            )
        )

    def review_card(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        topic_id: uuid.UUID,
        flashcard_id: uuid.UUID,
        rating: int,
        reviewed_at: datetime | None = None,
    ) -> Flashcard:
        """Apply one review and append it to the card's history.

        Idempotent in the sense that matters here: the same rating applied twice
        is two reviews with two history rows, and the second one starts from the
        state the first left behind. Nothing is double-counted, because the
        schedule is computed from stored state rather than from a count of rows.
        """
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        if not MIN_RATING <= rating <= MAX_RATING:
            raise StudyError(
                f"التقييم يجب أن يكون بين {MIN_RATING} و {MAX_RATING}.", code="rating_out_of_range"
            )
        card = db.scalar(
            select(Flashcard).where(Flashcard.id == flashcard_id, Flashcard.topic_id == topic.id)
        )
        if card is None:
            raise NotFoundError("البطاقة")

        moment = reviewed_at or datetime.now(UTC)
        interval_before = card.interval_days
        next_state = schedule(
            ease_factor=card.ease_factor,
            interval_days=card.interval_days,
            repetitions=card.repetitions,
            rating=rating,
            reviewed_at=moment,
        )

        db.add(
            FlashcardReview(
                id=uuid.uuid4(),
                flashcard_id=card.id,
                rating=rating,
                interval_before_days=interval_before,
                interval_after_days=next_state.interval_days,
            )
        )
        card.ease_factor = next_state.ease_factor
        card.interval_days = next_state.interval_days
        card.repetitions = next_state.repetitions
        card.due_at = next_state.due_at
        card.review_count += 1
        db.flush()
        return card

    # -- study plans ----------------------------------------------------

    def add_plan_item(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        topic_id: uuid.UUID,
        objective: str,
        scheduled_for: str,
    ) -> StudyPlanItem:
        from datetime import date as date_type

        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        if not objective.strip():
            raise StudyError("الهدف مطلوب.", code="objective_required")
        try:
            day = date_type.fromisoformat(scheduled_for)
        except ValueError as exc:
            raise StudyError("التاريخ يجب أن يكون بصيغة YYYY-MM-DD.", code="invalid_date") from exc

        item = StudyPlanItem(
            id=uuid.uuid4(),
            topic_id=topic.id,
            objective=objective.strip(),
            scheduled_for=day,
            state="not_started",
        )
        db.add(item)
        db.flush()
        return item

    def set_plan_state(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        topic_id: uuid.UUID,
        item_id: uuid.UUID,
        state: str,
    ) -> StudyPlanItem:
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        if state not in PLAN_STATES:
            raise StudyError("حالة غير مدعومة.", code="unsupported_state")
        item = db.scalar(
            select(StudyPlanItem).where(
                StudyPlanItem.id == item_id, StudyPlanItem.topic_id == topic.id
            )
        )
        if item is None:
            raise NotFoundError("العنصر")
        item.state = state
        item.completed_at = datetime.now(UTC) if state == "completed" else None
        db.flush()
        return item

    def plan_summary(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID
    ) -> dict[str, object]:
        """Completed vs outstanding, with missing material accounted for.

        Progress is a plain ratio of completed items to all items. There is no
        weighting and no smoothing: the number means exactly what it looks like,
        so a user can check it by counting.
        """
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        items = list(
            db.scalars(
                select(StudyPlanItem)
                .where(StudyPlanItem.topic_id == topic.id)
                .order_by(StudyPlanItem.scheduled_for)
            )
        )
        completed = [i for i in items if i.state == "completed"]
        outstanding = [i for i in items if i.state != "completed"]
        total = len(items)
        return {
            "total": total,
            "completed": len(completed),
            "outstanding": len(outstanding),
            "progress": round(len(completed) / total, 4) if total else 0.0,
            "by_state": {state: sum(1 for i in items if i.state == state) for state in PLAN_STATES},
            "items": [
                {
                    "id": str(i.id),
                    "objective": i.objective,
                    "scheduled_for": i.scheduled_for.isoformat(),
                    "state": i.state,
                    "material_note": i.material_note,
                }
                for i in items
            ],
        }

    def refresh_material_availability(
        self, db: Session, *, owner_id: uuid.UUID, topic_id: uuid.UUID
    ) -> int:
        """Mark flashcards whose source document has been deleted.

        A card whose provenance is gone must not keep presenting its locator as
        though it could still be checked. Returns how many cards changed.
        """
        topic = self.get_topic(db, owner_id=owner_id, topic_id=topic_id)
        changed = 0
        for card in db.scalars(select(Flashcard).where(Flashcard.topic_id == topic.id)):
            if card.source_document_id is None:
                continue
            exists = db.scalar(select(Document.id).where(Document.id == card.source_document_id))
            available = exists is not None
            if available != card.source_available:
                card.source_available = available
                changed += 1
        db.flush()
        return changed


def _normalize_for_comparison(text: str) -> str:
    """Lowercase and strip punctuation so comparison is not defeated by formatting."""
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\s\W_]+", " ", folded, flags=re.UNICODE).strip()
