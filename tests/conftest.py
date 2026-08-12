from pathlib import Path

import pytest

from src.models import Student
from src.rule_engine import RuleEngine


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine() -> RuleEngine:
    return RuleEngine(ROOT / "data" / "rules.yaml")


@pytest.fixture
def eligible_student() -> Student:
    return Student(
        student_id="TEST-OK",
        name="テスト学生",
        faculty="工学部",
        grade=4,
        earned_credits=124,
        current_registered_credits=20,
        required_a=True,
        required_b=True,
        registration_completed=True,
    )
