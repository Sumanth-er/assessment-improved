"""LLM grading of Python coding answers (Admin > Settings > Coding evaluation).

The database tests need AUDIT_TEST_DATABASE_URL pointing at a disposable,
migrated PostgreSQL database; they delete every row they create.
"""

import asyncio
import json
import os
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.llms import AIServiceUnavailable
from core import coding_llm_review, evaluation_service
from core.coding_llm_review import (
    CODING_LLM_SETTING_KEY,
    CodingLLMResult,
    CodingLLMSettings,
    evaluate_code_with_llm,
    save_coding_llm_settings,
)
from core.evaluation_service import _evaluate_coding_with_llm

DATABASE_URL = os.getenv("AUDIT_TEST_DATABASE_URL")
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="requires a disposable PostgreSQL database")


class _FakeModel:
    def __init__(self, result):
        self.prompts = []
        self.result = result

    def with_structured_output(self, schema, **_kwargs):
        assert schema is CodingLLMResult
        model = self

        class _Structured:
            def invoke(self, prompt):
                model.prompts.append(prompt)
                if isinstance(model.result, Exception):
                    raise model.result
                return model.result

        return _Structured()


def _use_model(monkeypatch, result):
    model = _FakeModel(result)
    monkeypatch.setattr(coding_llm_review, "get_chat_model", lambda: model)
    return model


def _forbid_test_case_runs(monkeypatch):
    import core.code_runner

    monkeypatch.setattr(core.code_runner, "run_test_case", lambda *_args: pytest.fail("test cases must not run"))
    monkeypatch.setattr(evaluation_service, "run_sql_test_case", lambda *_args: pytest.fail("SQL must not run"))


def test_llm_score_is_clamped_and_feedback_is_marked_as_ai_review(monkeypatch):
    model = _use_model(monkeypatch, CodingLLMResult(score=1.7, feedback="Correct and handles an empty list."))
    code = "def total(xs):\n    return sum(xs)"

    score, feedback = evaluate_code_with_llm(
        "Return the sum of a list",
        code,
        reference_answer="return sum(xs)",
        examples=[("[1, 2]", "3")],
        instructions="Be strict about edge cases.",
    )

    assert score == 1.0
    assert feedback == "AI review: Correct and handles an empty list."
    prompt = model.prompts[0]
    assert json.dumps(code) in prompt  # the code is passed as untrusted JSON data
    assert "Be strict about edge cases." in prompt
    assert '"expected_output": "3"' in prompt
    assert "return sum(xs)" in prompt


def test_default_grading_instructions_are_used_when_the_prompt_is_blank(monkeypatch):
    model = _use_model(monkeypatch, CodingLLMResult(score=0.5, feedback="Partly correct."))
    assert evaluate_code_with_llm("Question", "print(1)", instructions="   ") == (0.5, "AI review: Partly correct.")
    assert coding_llm_review.DEFAULT_CODING_LLM_INSTRUCTIONS in model.prompts[0]


def test_blank_code_scores_zero_without_calling_the_llm(monkeypatch):
    monkeypatch.setattr(coding_llm_review, "get_chat_model", lambda: pytest.fail("must not call the LLM"))
    assert evaluate_code_with_llm("Question", "  \n ") == (0.0, "No code provided.")


def test_provider_failure_is_raised_for_retry_instead_of_scoring_zero(monkeypatch):
    _use_model(monkeypatch, RuntimeError("429 rate limit"))
    with pytest.raises(AIServiceUnavailable):
        evaluate_code_with_llm("Question", "print(1)")


def test_template_is_assembled_and_item_test_cases_are_only_context(monkeypatch):
    model = _use_model(monkeypatch, CodingLLMResult(score=0.9, feedback="Good."))
    _forbid_test_case_runs(monkeypatch)
    item = SimpleNamespace(
        question_text="Square a number",
        reference_answer=None,
        default_code="# --- EDITABLE START ---\npass\n# --- EDITABLE END ---\nprint(square(int(input())))",
        test_cases=[{"input": "3", "expected_output": "9"}],
    )

    score, _ = _evaluate_coding_with_llm(item, "def square(n):\n    return n * n", "")

    assert score == 0.9
    prompt = model.prompts[0]
    assert "Square a number" in prompt
    assert "def square(n):\\n    return n * n\\n# --- EDITABLE END ---" in prompt
    assert '"input": "3"' in prompt


def test_question_bank_test_case_rows_are_passed_as_examples(monkeypatch):
    model = _use_model(monkeypatch, CodingLLMResult(score=0.0, feedback="Wrong output."))
    question = SimpleNamespace(
        question="Print the input twice",
        reference_answer="print(input() * 2)",
        default_code=None,
        test_cases=[SimpleNamespace(input_data="ab", expected_output="abab")],
    )

    assert _evaluate_coding_with_llm(question, "print(input())", "") == (0.0, "AI review: Wrong output.")
    assert '"expected_output": "abab"' in model.prompts[0]


def test_grading_prompt_length_is_limited():
    with pytest.raises(ValidationError):
        CodingLLMSettings(enabled=True, prompt="x" * 4001)


# ---------------- database-backed evaluation ---------------- #


@pytest.fixture(scope="module")
def audit_engine():
    url = make_url(DATABASE_URL)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    engine = create_engine(url)
    yield engine
    engine.dispose()


def _seed(engine, default_code=None, answer_text="n = int(input())\nprint(n * n)"):
    from db.models import (
        Answer, Assessment, AssessmentQuestion, Attempt, AttemptStatus, Difficulty,
        Question, QuestionType, TestCase, Topic, User, UserRole,
    )

    suffix = uuid.uuid4().hex[:10]
    with Session(engine) as db:
        user = User(username=f"llmcode_{suffix}", email=f"llmcode_{suffix}@test.invalid",
                    contact_email=f"llmcode_{suffix}@test.invalid", name="Candidate",
                    hashed_password="unused", role=UserRole.user)
        topic = Topic(name=f"llmcode_topic_{suffix}")
        assessment = Assessment(title=f"LLM coding {suffix}", duration=30)
        db.add_all([user, topic, assessment])
        db.flush()
        question = Question(topic_id=topic.id, type=QuestionType.coding, difficulty=Difficulty.easy,
                            question="Print the square of the input number",
                            reference_answer="n = int(input())\nprint(n * n)", default_code=default_code)
        db.add(question)
        db.flush()
        db.add(TestCase(question_id=question.id, input_data="3", expected_output="9", is_sample=True))
        db.add(AssessmentQuestion(assessment_id=assessment.id, question_id=question.id, position=0))
        attempt = Attempt(user_id=user.id, assessment_id=assessment.id,
                          status=AttemptStatus.completed, tab_violations=0)
        db.add(attempt)
        db.flush()
        db.add(Answer(attempt_id=attempt.id, question_id=question.id, answer=answer_text))
        db.commit()
        return SimpleNamespace(user_id=user.id, topic_id=topic.id, assessment_id=assessment.id,
                               question_id=question.id, attempt_id=attempt.id)


def _cleanup(engine, ids):
    from db.models import (
        AppSetting, Answer, Assessment, AssessmentQuestion, Attempt, Question, TestCase,
        Topic, TopicScore, User,
    )

    with Session(engine) as db:
        db.query(Answer).filter_by(attempt_id=ids.attempt_id).delete()
        db.query(TopicScore).filter_by(attempt_id=ids.attempt_id).delete()
        db.query(Attempt).filter_by(id=ids.attempt_id).delete()
        db.query(AssessmentQuestion).filter_by(assessment_id=ids.assessment_id).delete()
        db.query(TestCase).filter_by(question_id=ids.question_id).delete()
        db.query(Question).filter_by(id=ids.question_id).delete()
        db.query(Topic).filter_by(id=ids.topic_id).delete()
        db.query(Assessment).filter_by(id=ids.assessment_id).delete()
        db.query(User).filter_by(id=ids.user_id).delete()
        db.query(AppSetting).filter_by(key=CODING_LLM_SETTING_KEY).delete()
        db.commit()


def _answer_and_attempt(engine, ids):
    from db.models import Answer, Attempt

    with Session(engine) as db:
        return (
            db.query(Answer).filter_by(attempt_id=ids.attempt_id).one(),
            db.get(Attempt, ids.attempt_id),
        )


def _set_llm_grading(engine, enabled, prompt=""):
    with Session(engine) as db:
        save_coding_llm_settings(db, CodingLLMSettings(enabled=enabled, prompt=prompt))


@requires_db
def test_enabled_setting_grades_python_coding_with_the_llm_not_test_cases(audit_engine, monkeypatch):
    from db.models import EvaluationType

    ids = _seed(audit_engine)
    try:
        _set_llm_grading(audit_engine, True, "Full marks only when input is validated.")
        model = _use_model(monkeypatch, CodingLLMResult(score=0.75, feedback="Correct, but no input validation."))
        _forbid_test_case_runs(monkeypatch)

        with Session(audit_engine) as db:
            evaluation_service.evaluate_attempt_from_job(db, ids.attempt_id)

        answer, attempt = _answer_and_attempt(audit_engine, ids)
        assert answer.score == 0.75
        assert answer.evaluated_by == EvaluationType.llm
        assert answer.feedback == "AI review: Correct, but no input validation."
        assert attempt.score == 75.0
        assert "Full marks only when input is validated." in model.prompts[0]
        assert "Print the square of the input number" in model.prompts[0]

        # A retried evaluation job keeps the LLM score instead of grading again.
        monkeypatch.setattr(coding_llm_review, "get_chat_model", lambda: pytest.fail("must not re-grade"))
        with Session(audit_engine) as db:
            evaluation_service.evaluate_attempt_from_job(db, ids.attempt_id)
        assert _answer_and_attempt(audit_engine, ids)[1].score == 75.0
    finally:
        _cleanup(audit_engine, ids)


@requires_db
def test_disabled_setting_keeps_test_case_scoring(audit_engine, monkeypatch):
    import core.code_runner
    from db.models import EvaluationType

    ids = _seed(audit_engine)
    try:
        _set_llm_grading(audit_engine, False, "ignored while disabled")
        monkeypatch.setattr(coding_llm_review, "get_chat_model", lambda: pytest.fail("must not call the LLM"))
        monkeypatch.setattr(core.code_runner, "run_test_case", lambda *_args: {"passed": True, "error": None})

        with Session(audit_engine) as db:
            evaluation_service.evaluate_attempt_from_job(db, ids.attempt_id)

        answer, attempt = _answer_and_attempt(audit_engine, ids)
        assert (answer.score, answer.evaluated_by) == (1.0, EvaluationType.auto)
        assert answer.feedback == "All 1 test cases passed."
        assert attempt.score == 100.0
    finally:
        _cleanup(audit_engine, ids)


@requires_db
def test_sql_coding_questions_keep_test_case_scoring_when_llm_grading_is_on(audit_engine, monkeypatch):
    from db.models import EvaluationType

    ids = _seed(audit_engine, default_code="-- Write one SELECT query", answer_text="SELECT 9")
    try:
        _set_llm_grading(audit_engine, True)
        monkeypatch.setattr(coding_llm_review, "get_chat_model", lambda: pytest.fail("must not call the LLM"))
        monkeypatch.setattr(evaluation_service, "run_sql_test_case", lambda *_args: {"passed": True, "error": None})

        with Session(audit_engine) as db:
            evaluation_service.evaluate_attempt_from_job(db, ids.attempt_id)

        answer, _ = _answer_and_attempt(audit_engine, ids)
        assert (answer.score, answer.evaluated_by) == (1.0, EvaluationType.auto)
    finally:
        _cleanup(audit_engine, ids)


@requires_db
def test_admin_settings_endpoints_save_and_return_the_setting(audit_engine, monkeypatch):
    from api import routers_admin
    from db.models import AppSetting

    monkeypatch.setattr(routers_admin, "SessionLocal", lambda: Session(audit_engine))
    admin = SimpleNamespace(id=uuid.uuid4())
    try:
        with Session(audit_engine) as db:
            db.query(AppSetting).filter_by(key=CODING_LLM_SETTING_KEY).delete()
            db.commit()
        initial = asyncio.run(routers_admin.get_coding_evaluation_settings(admin))
        assert (initial["enabled"], initial["prompt"]) == (False, "")
        assert initial["default_prompt"] == coding_llm_review.DEFAULT_CODING_LLM_INSTRUCTIONS

        saved = asyncio.run(routers_admin.update_coding_evaluation_settings(
            CodingLLMSettings(enabled=True, prompt="  Check time complexity.  "), admin
        ))
        assert (saved["enabled"], saved["prompt"]) == (True, "Check time complexity.")
        asyncio.run(routers_admin.update_coding_evaluation_settings(CodingLLMSettings(enabled=False), admin))

        current = asyncio.run(routers_admin.get_coding_evaluation_settings(admin))
        assert (current["enabled"], current["prompt"]) == (False, "")
    finally:
        with Session(audit_engine) as db:
            db.query(AppSetting).filter_by(key=CODING_LLM_SETTING_KEY).delete()
            db.commit()
