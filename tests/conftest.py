import copy
from pathlib import Path

import pytest

from src.examples import build_example_graph, example_student_iri
from src.knowledge_graph import KnowledgeGraph
from src.models import Student
from src.rule_engine import RuleEngine


ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "data" / "university_graph.jsonld"
RULES_PATH = ROOT / "data" / "rules.yaml"

# TEST-OK：4年生、修得124単位（必修A・Bを含む）、2026年度前期に20単位を履修登録（登録完了）。
# 判定はナレッジグラフ上の事実だけから行うので、テスト用の学生も例題と同じく
# 修得の記録・履修登録としてグラフへ置く（build_example_graph）。
TEST_OK_PROFILE = {
    "year_of_study": 4,
    "history": [
        {"academic_year": 2023, "year_of_study": 1, "courses": ["REQ-A"], "other_credits": 36},  # 40
        {"academic_year": 2024, "year_of_study": 2, "courses": ["REQ-B"], "other_credits": 40},  # 44
        {"academic_year": 2025, "year_of_study": 3, "other_credits": 40},  # 40 → 合計124
    ],
    "registration": {"courses": [], "other_credits": 20, "completed": True},
}


@pytest.fixture
def graph() -> KnowledgeGraph:
    return KnowledgeGraph(GRAPH_PATH)


@pytest.fixture
def engine(graph: KnowledgeGraph) -> RuleEngine:
    return RuleEngine(RULES_PATH, graph)


@pytest.fixture
def eligible_profile() -> dict:
    """TEST-OK のプロファイル（テストごとに書き換えてよい写し）。"""
    return copy.deepcopy(TEST_OK_PROFILE)


@pytest.fixture
def student_on_graph(graph: KnowledgeGraph):
    """プロファイル（dict）→（そのグラフで作った RuleEngine, グラフから射影された Student）。"""

    def make(
        profile: dict, *, rules_path: Path = RULES_PATH, example_id: str = "TEST-OK"
    ) -> tuple[RuleEngine, Student]:
        example_graph = build_example_graph({"id": example_id, "student": profile}, graph)
        iri = example_student_iri(example_id)
        student = next(item for item in example_graph.students() if item.iri == iri)
        return RuleEngine(rules_path, example_graph), student

    return make


@pytest.fixture
def eligible_case(student_on_graph, eligible_profile) -> tuple[RuleEngine, Student]:
    """TEST-OK の（RuleEngine, Student）。"""
    return student_on_graph(eligible_profile)
