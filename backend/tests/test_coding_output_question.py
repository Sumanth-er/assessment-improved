"""Output-only coding questions: no test cases, no LLM, score 1 when the code runs."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

import api.routers_code as code_api
import core.code_runner
from core import evaluation_service
from core.evaluation_service import _evaluate_coding_output
from db.models import QuestionType, TestCase


def _question(default_code=None):
    return SimpleNamespace(id=uuid4(), type=QuestionType.coding_output, default_code=default_code)


def test_code_that_runs_scores_one_and_reports_output(monkeypatch):
    calls = []

    def run(code, input_data, expected_output):
        calls.append((code, input_data, expected_output))
        return {"passed": False, "actual_output": "55", "error": None, "exit_code": 0}

    monkeypatch.setattr(core.code_runner, "run_test_case", run)
    score, feedback = _evaluate_coding_output(_question(), "print(sum(range(1, 11)))")

    assert score == 1.0
    assert feedback == "Code ran successfully. Output: 55"
    # Runs once, with no stdin and no expected output.
    assert calls == [("print(sum(range(1, 11)))", "", "")]


def test_code_with_an_error_scores_zero(monkeypatch):
    monkeypatch.setattr(core.code_runner, "run_test_case", lambda *_args: {
        "passed": False, "actual_output": "",
        "error": "NameError: name 'x' is not defined", "exit_code": 1,
    })
    score, feedback = _evaluate_coding_output(_question(), "print(x)")

    assert score == 0.0
    assert feedback == "Code did not run: NameError: name 'x' is not defined"


def test_blank_code_scores_zero_without_running(monkeypatch):
    monkeypatch.setattr(core.code_runner, "run_test_case", lambda *_args: pytest.fail("must not run"))
    assert _evaluate_coding_output(_question(), "   ") == (0.0, "No code provided.")


def test_unavailable_runner_is_retried_not_scored_zero(monkeypatch):
    monkeypatch.setattr(core.code_runner, "run_test_case", lambda *_args: {
        "passed": False, "actual_output": "",
        "error": "Python execution unavailable: capacity is busy", "exit_code": -1,
    })
    with pytest.raises(RuntimeError, match="execution unavailable"):
        _evaluate_coding_output(_question(), "print(1)")


def test_template_is_assembled_around_the_candidate_code(monkeypatch):
    template = "# --- EDITABLE START ---\npass\n# --- EDITABLE END ---\nprint(square(7))"
    seen = {}

    def run(code, *_args):
        seen["code"] = code
        return {"passed": False, "actual_output": "49", "error": None, "exit_code": 0}

    monkeypatch.setattr(core.code_runner, "run_test_case", run)
    score, _ = _evaluate_coding_output(_question(template), "def square(n):\n    return n * n")

    assert score == 1.0
    assert seen["code"].startswith("# --- EDITABLE START ---\ndef square(n):")
    assert seen["code"].endswith("# --- EDITABLE END ---\nprint(square(7))")


def test_sql_query_that_runs_scores_one_even_though_nothing_is_expected(monkeypatch):
    # The SQL runner reports "Wrong Answer" whenever rows differ from the (empty)
    # expected output; for output-only questions only a successful run matters.
    monkeypatch.setattr(evaluation_service, "run_sql_test_case", lambda *_args: {
        "passed": False, "actual_output": "Alice,90000", "error": "Wrong Answer", "exit_code": 0,
    })
    score, feedback = _evaluate_coding_output(
        _question("-- Write one SELECT query"), "SELECT name, salary FROM employees"
    )

    assert score == 1.0
    assert feedback == "Code ran successfully. Output: Alice,90000"


class _FakeQuery:
    def __init__(self, question):
        self.question = question

    def filter(self, *_conditions):
        return self

    def first(self):
        return self.question


class _FakeDB:
    def __init__(self, question):
        self.question = question
        self.models = []

    def query(self, model):
        self.models.append(model)
        return _FakeQuery(self.question)

    def close(self):
        pass


def _run(monkeypatch, question, runner_result):
    database = _FakeDB(question)
    monkeypatch.setattr(code_api, "SessionLocal", lambda: database)

    async def run_sync(function):
        return function()

    monkeypatch.setattr(code_api, "run_db_sync", run_sync)
    monkeypatch.setattr(code_api, "run_test_case", lambda **_kwargs: runner_result)
    response = asyncio.run(code_api.run_code(
        code_api.RunCodeRequest(question_id=question.id, code="print('hi')", language="python"),
        _user=SimpleNamespace(id=uuid4()),
    ))
    return response, database


def test_run_endpoint_returns_program_output_without_test_cases(monkeypatch):
    response, database = _run(monkeypatch, _question(), {
        "passed": False, "actual_output": "hi", "expected_output": "", "error": None, "exit_code": 0,
    })

    assert TestCase not in database.models
    assert response.total == 1
    assert response.all_passed is True
    assert response.results[0].actual_output == "hi"
    assert response.results[0].error is None


def test_run_endpoint_reports_the_error_when_code_fails(monkeypatch):
    response, _ = _run(monkeypatch, _question(), {
        "passed": False, "actual_output": "", "expected_output": "",
        "error": "ZeroDivisionError: division by zero", "exit_code": 1,
    })

    assert response.all_passed is False
    assert response.results[0].error == "ZeroDivisionError: division by zero"
