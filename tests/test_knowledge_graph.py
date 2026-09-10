"""ナレッジグラフの射影が、デモの前提値と一致することを検証する。"""

import json
from pathlib import Path

import pytest

from src.knowledge_graph import KnowledgeGraph, KnowledgeGraphError


ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "S001": {"name": "山田太郎", "year": 3, "earned": 92, "registered": 36, "completed_required": ["REQ-A"]},
    "S002": {"name": "鈴木花子", "year": 4, "earned": 118, "registered": 20, "completed_required": ["REQ-A", "REQ-B"]},
    "S003": {"name": "佐藤次郎", "year": 4, "earned": 126, "registered": 12, "completed_required": ["REQ-A", "REQ-B"]},
}


def test_students_are_projected_from_the_graph(graph):
    students = {student.student_id: student for student in graph.students()}
    assert set(students) == set(EXPECTED)
    for student_id, expected in EXPECTED.items():
        student = students[student_id]
        assert student.name == expected["name"]
        assert student.year_of_study == expected["year"]
        assert student.earned_credits == expected["earned"]
        assert student.current_registered_credits == expected["registered"]
        assert student.faculty == "工学部"
        assert student.iri == f"urdi:student/{student_id}"
        for code in expected["completed_required"]:
            assert code in student.completed_courses


def test_credit_totals_are_derived_not_stored(graph):
    """単位数はグラフに書かれた数値ではなく、科目をたどった合計であること。"""
    raw = (ROOT / "data" / "university_graph.jsonld").read_text(encoding="utf-8")
    assert "urd:earnedCredits" not in raw
    assert "urd:registeredCredits" not in raw

    student = next(s for s in graph.students() if s.student_id == "S001")
    recomputed = sum(graph.course(code).credits for code in student.completed_courses)
    assert recomputed == student.earned_credits == 92


def test_registration_status_comes_from_register_action(graph):
    students = {student.student_id: student for student in graph.students()}
    assert students["S001"].registration_completed is False
    assert students["S002"].registration_completed is True
    assert students["S003"].registration_completed is False


def test_course_catalog_is_indexed_by_course_code(graph):
    assert graph.has_course("REQ-A")
    assert graph.course("REQ-A").name == "必修A"
    assert graph.course("THESIS").credits == 8
    assert graph.course_label("REQ-B") == "必修B"
    assert not graph.has_course("NOT-IN-GRAPH")
    with pytest.raises(KnowledgeGraphError):
        graph.course("NOT-IN-GRAPH")


def test_legislation_articles_resolve_to_their_parent(graph):
    article = graph.legislation("urdi:legislation/gakusoku-2026/art32-1")
    assert article is not None
    assert article.identifier == "第32条第1項"
    assert article.parent_name == "2026年度学則"
    assert article.citation == "2026年度学則 第32条第1項"
    assert graph.legislation("urdi:legislation/missing") is None
    assert len(graph.legislation_articles()) == 5


def test_program_is_unique(graph):
    program = graph.program()
    assert program["@id"] == "urdi:program/engineering-2026"
    assert "ccso:ProgramofStudy" in graph.types_of(program)


def test_missing_context_prefix_is_rejected(tmp_path):
    document = {
        "@context": {"schema": "https://schema.org/"},
        "@graph": [{"@id": "urdi:x", "@type": "schema:Person"}],
    }
    path = tmp_path / "broken.jsonld"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KnowledgeGraphError, match="接頭辞"):
        KnowledgeGraph(path)


def test_duplicate_node_id_is_rejected(tmp_path, graph):
    document = json.loads((ROOT / "data" / "university_graph.jsonld").read_text(encoding="utf-8"))
    document["@graph"].append(dict(document["@graph"][0]))
    path = tmp_path / "duplicated.jsonld"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(KnowledgeGraphError, match="重複"):
        KnowledgeGraph(path)
