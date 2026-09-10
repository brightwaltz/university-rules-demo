from pathlib import Path

import pytest

from src.knowledge_graph import KnowledgeGraph
from src.models import Student
from src.rule_engine import RuleEngine


ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "data" / "university_graph.jsonld"
RULES_PATH = ROOT / "data" / "rules.yaml"


@pytest.fixture
def graph() -> KnowledgeGraph:
    return KnowledgeGraph(GRAPH_PATH)


@pytest.fixture
def engine(graph: KnowledgeGraph) -> RuleEngine:
    return RuleEngine(RULES_PATH, graph)


@pytest.fixture
def eligible_student() -> Student:
    return Student(
        student_id="TEST-OK",
        name="テスト学生",
        faculty="工学部",
        year_of_study=4,
        earned_credits=124,
        current_registered_credits=20,
        completed_courses=["REQ-A", "REQ-B"],
        registered_courses=[],
        registration_completed=True,
        iri="urdi:student/TEST-OK",
    )
