"""Admin "Resume" for attempts stopped by the tab-violation (exit-event) limit.

Needs AUDIT_TEST_DATABASE_URL pointing at a disposable, migrated PostgreSQL
database; every row created here is deleted afterwards.
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

DATABASE_URL = os.getenv("AUDIT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a disposable PostgreSQL database")


@pytest.fixture(scope="module")
def audit_engine():
    url = make_url(DATABASE_URL)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def routers(audit_engine, monkeypatch):
    from api import routers_admin, routers_user_assessments

    monkeypatch.setattr(routers_admin, "SessionLocal", lambda: Session(audit_engine))
    monkeypatch.setattr(routers_user_assessments, "SessionLocal", lambda: Session(audit_engine))
    return SimpleNamespace(admin=routers_admin, user=routers_user_assessments)


def _seed_stopped_attempt(engine, *, end_time=None, active_seconds=600.0, job_status="completed"):
    """An attempt the exit-event limit stopped, after its partial answers were evaluated."""
    from db.models import (
        Answer, Assessment, AssessmentQuestion, AssessmentUserAssignment, Attempt,
        AttemptIntegrityEvent, AttemptStatus, Difficulty, EvaluationJob, EvaluationJobStatus,
        EvaluationType, Question, QuestionType, Topic, TopicScore, User, UserRole,
    )

    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        user = User(username=f"resume_{suffix}", email=f"resume_{suffix}@test.invalid",
                    contact_email=f"resume_{suffix}@test.invalid", name="Candidate",
                    hashed_password="unused", role=UserRole.user)
        topic = Topic(name=f"resume_topic_{suffix}")
        assessment = Assessment(title=f"Resume test {suffix}", duration=30, end_time=end_time)
        db.add_all([user, topic, assessment])
        db.flush()
        questions = [
            Question(topic_id=topic.id, type=QuestionType.text, difficulty=Difficulty.easy,
                     question=f"Explain concept {index}", reference_answer="Reference")
            for index in range(2)
        ]
        db.add_all(questions)
        db.flush()
        for position, question in enumerate(questions):
            db.add(AssessmentQuestion(assessment_id=assessment.id, question_id=question.id, position=position))
        db.add(AssessmentUserAssignment(assessment_id=assessment.id, user_id=user.id))
        attempt = Attempt(user_id=user.id, assessment_id=assessment.id, status=AttemptStatus.missed,
                          started_at=now - timedelta(minutes=15), submitted_at=now - timedelta(minutes=1),
                          active_seconds=active_seconds, tab_violations=5, score=50.0)
        db.add(attempt)
        db.flush()
        db.add(Answer(attempt_id=attempt.id, question_id=questions[0].id, answer="My saved answer",
                      score=1.0, evaluated_by=EvaluationType.llm, feedback="Good"))
        db.add(TopicScore(attempt_id=attempt.id, topic_id=topic.id, score=50.0))
        db.add(EvaluationJob(attempt_id=attempt.id, status=EvaluationJobStatus(job_status)))
        db.add(AttemptIntegrityEvent(attempt_id=attempt.id, event_id=uuid.uuid4(), event_type="focus_lost"))
        db.commit()
        return SimpleNamespace(user_id=user.id, topic_id=topic.id, assessment_id=assessment.id,
                               question_ids=[q.id for q in questions], attempt_id=attempt.id)


def _cleanup(engine, ids):
    from db.models import (
        Answer, Assessment, AssessmentQuestion, AssessmentUserAssignment, Attempt,
        AttemptIntegrityEvent, EvaluationJob, Question, Topic, TopicScore, User,
    )

    with Session(engine) as db:
        attempt_ids = [row.id for row in db.query(Attempt).filter_by(assessment_id=ids.assessment_id)]
        for model in (AttemptIntegrityEvent, Answer, TopicScore, EvaluationJob):
            db.query(model).filter(model.attempt_id.in_(attempt_ids)).delete(synchronize_session=False)
        db.query(Attempt).filter(Attempt.id.in_(attempt_ids)).delete(synchronize_session=False)
        db.query(AssessmentQuestion).filter_by(assessment_id=ids.assessment_id).delete()
        db.query(AssessmentUserAssignment).filter_by(assessment_id=ids.assessment_id).delete()
        db.query(Question).filter(Question.id.in_(ids.question_ids)).delete(synchronize_session=False)
        db.query(Topic).filter_by(id=ids.topic_id).delete()
        db.query(Assessment).filter_by(id=ids.assessment_id).delete()
        db.query(User).filter_by(id=ids.user_id).delete()
        db.commit()


def test_resume_reopens_the_same_attempt_with_answers_time_and_zero_violations(audit_engine, routers):
    from db.models import (
        Answer, Attempt, AttemptIntegrityEvent, AttemptStatus, EvaluationJob, TopicScore,
    )

    ids = _seed_stopped_attempt(audit_engine)
    try:
        response = asyncio.run(routers.admin.resume_user_attempt(ids.attempt_id, SimpleNamespace()))
        assert "violation count reset" in response["message"]

        with Session(audit_engine) as db:
            attempt = db.get(Attempt, ids.attempt_id)
            assert attempt.status == AttemptStatus.in_progress
            assert attempt.tab_violations == 0
            assert attempt.score is None and attempt.submitted_at is None
            assert attempt.active_seconds == 600.0
            answer = db.query(Answer).filter_by(attempt_id=ids.attempt_id).one()
            assert answer.answer == "My saved answer"
            assert (answer.score, answer.evaluated_by, answer.feedback) == (None, None, None)
            assert db.query(EvaluationJob).filter_by(attempt_id=ids.attempt_id).count() == 0
            assert db.query(TopicScore).filter_by(attempt_id=ids.attempt_id).count() == 0
            # The exit-event history is kept for audit.
            assert db.query(AttemptIntegrityEvent).filter_by(attempt_id=ids.attempt_id).count() == 1

        # The candidate's dashboard shows it as resumable, and starting it continues the same attempt.
        candidate = SimpleNamespace(id=ids.user_id)
        listed = asyncio.run(routers.user.get_my_assessments(candidate))
        assert next(item for item in listed if item.id == ids.assessment_id).status == "incomplete"
        resumed = asyncio.run(routers.user.start_assessment(ids.assessment_id, candidate))
        assert resumed.attempt_id == ids.attempt_id
        assert resumed.tab_violations == 0
        assert 1190 <= resumed.time_remaining_seconds <= 1200  # 30 min - 10 min already used
        assert [(a.question_id, a.answer) for a in resumed.saved_answers] == [
            (ids.question_ids[0], "My saved answer"),
        ]
        with Session(audit_engine) as db:
            assert db.query(Attempt).filter_by(assessment_id=ids.assessment_id).count() == 1
    finally:
        _cleanup(audit_engine, ids)


@pytest.mark.parametrize(
    "seed_kwargs,expected_detail",
    [
        ({"end_time": datetime.now(timezone.utc) - timedelta(minutes=1)}, "window has closed"),
        ({"active_seconds": 30 * 60.0}, "No assessment time is left"),
        ({"job_status": "in_progress"}, "being evaluated right now"),
    ],
)
def test_resume_is_refused_when_the_attempt_cannot_continue(audit_engine, routers, seed_kwargs, expected_detail):
    from db.models import Attempt, AttemptStatus

    ids = _seed_stopped_attempt(audit_engine, **seed_kwargs)
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(routers.admin.resume_user_attempt(ids.attempt_id, SimpleNamespace()))
        assert exc.value.status_code == 409
        assert expected_detail in exc.value.detail
        with Session(audit_engine) as db:
            attempt = db.get(Attempt, ids.attempt_id)
            assert (attempt.status, attempt.tab_violations) == (AttemptStatus.missed, 5)
    finally:
        _cleanup(audit_engine, ids)


def test_only_attempts_stopped_by_the_limit_can_be_resumed(audit_engine, routers):
    from db.models import Attempt, AttemptStatus

    ids = _seed_stopped_attempt(audit_engine)
    try:
        with Session(audit_engine) as db:
            db.get(Attempt, ids.attempt_id).status = AttemptStatus.completed
            db.commit()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(routers.admin.resume_user_attempt(ids.attempt_id, SimpleNamespace()))
        assert exc.value.status_code == 409
        assert "tab-violation limit" in exc.value.detail

        with pytest.raises(HTTPException) as missing:
            asyncio.run(routers.admin.resume_user_attempt(uuid.uuid4(), SimpleNamespace()))
        assert missing.value.status_code == 404
    finally:
        _cleanup(audit_engine, ids)
