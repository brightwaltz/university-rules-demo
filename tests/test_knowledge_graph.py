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
    # 学則 第32条第1項・第2項、履修規程 第5条・第12条・第14条、工学部履修細則 第18条
    assert len(graph.legislation_articles()) == 6


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


# --- 修得の時期（schema:AchieveAction） ------------------------------------


def _document() -> dict:
    return json.loads((ROOT / "data" / "university_graph.jsonld").read_text(encoding="utf-8"))


def _achievement(document: dict, iri: str) -> dict:
    return next(node for node in document["@graph"] if node["@id"] == iri)


def test_completion_records_split_earned_credits_by_year(graph):
    """年度ごとの修得記録は年度順に並び、合計が修得単位数と一致すること。"""
    expected = {
        "S001": [("2024", 1, 32), ("2025", 2, 60)],
        "S002": [("2023", 1, 28), ("2024", 2, 48), ("2025", 3, 42)],
        "S003": [("2023", 1, 36), ("2024", 2, 48), ("2025", 3, 42)],
    }
    students = {student.student_id: student for student in graph.students()}
    for student_id, rows in expected.items():
        records = graph.completion_records(f"urdi:student/{student_id}")
        assert [
            (record["academic_year"], record["year_of_study"], record["credits"])
            for record in records
        ] == rows, student_id
        assert sum(record["credits"] for record in records) == students[student_id].earned_credits
        codes = [code for record in records for code in record["course_codes"]]
        assert sorted(codes) == sorted(students[student_id].completed_courses), student_id

    first_year = graph.completion_records("urdi:student/S001")[0]
    assert first_year["course_codes"][:2] == ["REQ-A", "GEN-01"]
    assert graph.completion_records("urdi:student/NOT-IN-GRAPH") == []


def test_from_document_builds_the_same_graph(graph):
    rebuilt = KnowledgeGraph.from_document(_document())
    assert rebuilt.path is None
    assert rebuilt.students() == graph.students()
    assert rebuilt.completion_records("urdi:student/S002") == graph.completion_records(
        "urdi:student/S002"
    )


def test_completion_record_missing_a_course_is_rejected():
    """修得の記録から科目が欠けると、ccso:hasCompleted と一致しない。"""
    document = _document()
    record = _achievement(document, "urdi:achievement/S002/2023")
    record["schema:object"] = record["schema:object"][1:]
    with pytest.raises(KnowledgeGraphError, match="一致しません"):
        KnowledgeGraph.from_document(document)


def test_completion_record_with_an_uncompleted_course_is_rejected():
    document = _document()
    record = _achievement(document, "urdi:achievement/S001/2025")
    record["schema:object"].append({"@id": "urdi:course/REQ-B"})
    with pytest.raises(KnowledgeGraphError, match="一致しません"):
        KnowledgeGraph.from_document(document)


def test_course_in_two_completion_records_is_rejected():
    document = _document()
    record = _achievement(document, "urdi:achievement/S003/2024")
    record["schema:object"].append({"@id": "urdi:course/REQ-A"})
    with pytest.raises(KnowledgeGraphError, match="複数の修得記録"):
        KnowledgeGraph.from_document(document)


def test_every_article_carries_its_text(graph):
    articles = {article.iri: article for article in graph.legislation_articles()}
    for article in articles.values():
        assert article.text, article.iri
    article = articles["urdi:legislation/risyu-kitei-2026/art14"]
    assert article.citation == "2026年度履修規程 第14条"
    assert article.name == "第1年次に修得すべき単位数"
    assert article.text == (
        "学生は、第1年次において、卒業に必要な単位数の4分の1以上を修得しなければならない。"
        "2 前項に定める単位数を修得できなかった学生は、修学指導の対象とする。"
    )
