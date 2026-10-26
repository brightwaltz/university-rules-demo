from pathlib import Path

import pytest
import yaml

from src.answer_service import AnswerService
from src.horn import Var, atom
from src.intent import Intent, RuleBasedIntentDetector
from src.models import DecisionStatus, Student
from src.ontology import HORN_PROGRAM
from src.rule_engine import RuleEngine

ROOT = Path(__file__).resolve().parents[1]


def test_remaining_capacity_is_12_for_36_registered(student_on_graph, eligible_profile):
    eligible_profile["registration"]["other_credits"] = 36
    engine, student = student_on_graph(eligible_profile)
    result = engine.get_remaining_credit_capacity(student)
    assert result.facts["remaining_credits"] == 12
    assert result.rule_ids == ["RULE-CREDIT-001"]


def test_remaining_capacity_is_zero_for_48_registered(student_on_graph, eligible_profile):
    eligible_profile["registration"]["other_credits"] = 48
    engine, student = student_on_graph(eligible_profile)
    result = engine.get_remaining_credit_capacity(student)
    assert result.facts["remaining_credits"] == 0


def test_graduation_eligible_with_credits_and_required_courses(eligible_case):
    engine, student = eligible_case
    result = engine.evaluate_graduation(student)
    assert result.status == DecisionStatus.ELIGIBLE
    assert result.unmet_conditions == []


def test_graduation_ineligible_if_required_b_missing(student_on_graph, eligible_profile):
    # 2年次の必修B（4単位）をその他の科目に置き換え、合計124単位は保つ
    eligible_profile["history"][1] = {"academic_year": 2024, "year_of_study": 2, "other_credits": 44}
    engine, student = student_on_graph(eligible_profile)
    assert student.earned_credits == 124
    result = engine.evaluate_graduation(student)
    assert result.status == DecisionStatus.NOT_ELIGIBLE
    assert "必修Bを未修得" in result.unmet_conditions


def test_thesis_ineligible_below_100_credits(student_on_graph, eligible_profile):
    eligible_profile["history"][2]["other_credits"] = 15  # 40 + 44 + 15 = 99単位
    engine, student = student_on_graph(eligible_profile)
    assert student.earned_credits == 99
    result = engine.evaluate_thesis_eligibility(student)
    assert result.status == DecisionStatus.NOT_ELIGIBLE
    assert "修得単位が100単位未満" in result.unmet_conditions


def test_thesis_eligible_when_all_conditions_met(eligible_case):
    engine, student = eligible_case
    result = engine.evaluate_thesis_eligibility(student)
    assert result.status == DecisionStatus.ELIGIBLE


def test_decision_carries_ontology_terms_and_horn_clause(eligible_case):
    engine, student = eligible_case
    result = engine.evaluate_graduation(student)
    assert "schema:numberOfCredits" in result.ontology_terms
    assert "ccso:hasCompleted" in result.ontology_terms
    assert result.horn_clause is not None
    assert "eligible_to_graduate" in result.horn_clause


def test_premises_record_the_failing_atom(student_on_graph, eligible_profile):
    eligible_profile["history"][2]["other_credits"] = 16  # 40 + 44 + 16 = 100単位
    engine, student = student_on_graph(eligible_profile)
    result = engine.evaluate_graduation(student)
    credit_premise = next(p for p in result.premises if p.term == "urd:earnedCredits")
    assert credit_premise.actual == 100
    assert credit_premise.expected == 124
    assert credit_premise.comparator == ">="
    assert credit_premise.satisfied is False


def test_rule_reference_cites_the_legislation_article(engine):
    result = engine.get_registration_deadline()
    reference = result.rule_references[0]
    assert reference.legislation_iri == "urdi:legislation/risyu-kitei-2026/art5"
    assert reference.legislation_identifier == "2026年度履修規程 第5条"


# --- 処理系（Horn 節）による判定 ---------------------------------------------

def _students(graph):
    return {student.student_id: student for student in graph.students()}


@pytest.mark.parametrize(
    ("student_id", "earned", "status"),
    [
        ("S001", 32, DecisionStatus.ELIGIBLE),
        ("S002", 28, DecisionStatus.NOT_ELIGIBLE),
        ("S003", 36, DecisionStatus.ELIGIBLE),
    ],
)
def test_first_year_requirement(engine, graph, student_id, earned, status):
    result = engine.evaluate_first_year_requirement(_students(graph)[student_id])
    assert result.status == status
    assert result.facts == {
        "target_year_of_study": 1,
        "first_year_credits": earned,
        "required_credits": 124,
        "minimum_ratio_percent": 25,
        "minimum_credits": 31,
    }
    assert result.calculation == f"ceiling(124 × 25 / 100) = 31、1年次の修得 {earned} >= 31"
    assert result.rule_ids == ["RULE-FIRSTYEAR-001", "RULE-GRAD-001"]
    assert [p.term for p in result.premises] == ["urd:minimumCreditRatio", "urd:atYearOfStudy"]
    assert result.premises[0].actual == 31
    assert result.premises[0].subject == "urdi:program/engineering-2026"
    assert (result.premises[1].actual, result.premises[1].expected) == (earned, 31)
    assert result.premises[1].satisfied is (status == DecisionStatus.ELIGIBLE)
    assert result.horn_clause == HORN_PROGRAM.listing(
        ["first_year_requirement_applies/1", "meets_first_year_requirement/1"]
    )
    assert "schema:AchieveAction" in result.ontology_terms
    assert "urd:earnedCredits" in result.ontology_terms  # 卒業必要単位数の種別の用語も含む
    if status == DecisionStatus.ELIGIBLE:
        assert result.message == (
            f"1年次の修得単位要件を満たしています（1年次の修得 {earned}単位／必要 31単位）。"
        )
        assert result.unmet_conditions == []


def test_first_year_requirement_failure_message_and_proof(engine, graph):
    result = engine.evaluate_first_year_requirement(_students(graph)["S002"])
    assert result.message == (
        "1年次の修得単位要件を満たしていません（1年次の修得 28単位／必要 31単位）。"
        "履修規程第14条第2項により修学指導の対象となります。"
    )
    assert result.unmet_conditions == ["1年次の修得単位が3単位不足"]
    proof = result.proof
    assert proof["goal"] == "meets_first_year_requirement(urdi:student/S002)"
    assert proof["proved"] is False
    assert proof["reading"] == "鈴木花子が、1年次の修得単位要件を満たしている"
    # 失敗の説明は、1年次の単位・必要数までは証明でき、比較 28 >= 31 で止まる
    assert [(c["goal"], c["proved"]) for c in proof["children"]] == [
        ("first_year_credits(urdi:student/S002, 28)", True),
        ("first_year_minimum(urdi:student/S002, 31)", True),
        ("28 >= 31", False),
    ]
    last = proof["children"][-1]
    assert last["kind"] == "builtin" and last["reading"] == "28が31以上"


def test_transfer_student_is_outside_the_first_year_requirement(student_on_graph, graph, engine):
    """編入学生は第14条の対象外：満たす・満たさないを言わず UNKNOWN を返す。"""
    transfer, student = student_on_graph({"year_of_study": 3, "admission": "transfer", "history": []})
    result = transfer.evaluate_first_year_requirement(student)
    assert result.status == DecisionStatus.UNKNOWN
    assert result.message == "編入学のため、履修規程第14条（第1年次の修得単位）の対象外です。"
    assert result.unmet_conditions == []
    assert result.rule_ids == ["RULE-FIRSTYEAR-001"]
    assert result.facts == {
        "target_year_of_study": 1,
        "admission_category": "urdi:term/transfer-admission",
    }
    assert [p.term for p in result.premises] == ["urd:admissionCategory"]
    assert result.premises[0].satisfied is False
    # 画面には、足した適用条件の節も出る
    assert result.horn_clause.splitlines()[0] == "first_year_requirement_applies(Student) :-"
    assert "meets_first_year_requirement(Student) :-" in result.horn_clause
    proof = result.proof
    assert proof["goal"] == "first_year_requirement_applies(urdi:example/TEST-OK/student)"
    assert proof["proved"] is False
    assert proof["reading"] == "例題の学生に履修規程第14条が適用される"
    assert [(c["goal"], c["proved"]) for c in proof["children"]] == [
        ("ccso:enrolledIn(urdi:example/TEST-OK/student, urdi:program/engineering-2026)", True),
        ("\\+ urd:admissionCategory(urdi:example/TEST-OK/student, urdi:term/transfer-admission)", False),
    ]
    assert proof["children"][-1]["reading"] == "例題の学生の入学区分は編入学、ということはない"
    # 一般入学の学生は従来どおり（S002 は不足で NOT_ELIGIBLE）
    s002 = engine.evaluate_first_year_requirement(_students(graph)["S002"])
    assert s002.status == DecisionStatus.NOT_ELIGIBLE
    assert s002.proof["goal"] == "meets_first_year_requirement(urdi:student/S002)"


def test_admission_defaults_to_regular(student_on_graph):
    """admission を省略した例題の学生は一般入学として、第14条の対象になる。"""
    engine, student = student_on_graph({"year_of_study": 3, "history": []})
    assert engine.graph.ref(engine.graph.node(student.iri), "urd:admissionCategory") == (
        "urdi:term/regular-admission"
    )
    assert engine.evaluate_first_year_requirement(student).status == DecisionStatus.NOT_ELIGIBLE


@pytest.mark.parametrize("missing", ["first_year_credit_requirement", "graduation_credit_requirement"])
def test_first_year_requirement_needs_both_rules(graph, tmp_path, missing):
    raw = yaml.safe_load((ROOT / "data" / "rules.yaml").read_text(encoding="utf-8"))
    raw["rules"] = [rule for rule in raw["rules"] if rule["rule_type"] != missing]
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    engine = RuleEngine(path, graph)
    student = _students(graph)["S001"]
    with pytest.raises(ValueError, match=missing):
        engine.evaluate_first_year_requirement(student)
    intent, result = AnswerService(engine, RuleBasedIntentDetector()).answer(
        "1年次の単位要件は満たしていますか？", student
    )
    assert intent == Intent.FIRST_YEAR_REQUIREMENT
    assert result.status == DecisionStatus.UNKNOWN
    assert result.message == "このプロトタイプに登録されているルールでは判定できません。"


def test_solver_credits_match_the_graph_projection(engine, graph):
    """交差検証：処理系の派生用語の値が、KnowledgeGraph の射影値と一致する。"""
    for student in graph.students():
        assert engine.solver.value(atom("urd:earnedCredits", student.iri, Var("X")), "X") == (
            student.earned_credits
        ), student.student_id
        assert engine.solver.value(
            atom("urd:registeredCredits", student.iri, Var("X")), "X"
        ) == student.current_registered_credits, student.student_id


def test_decisions_on_graph_students_come_from_the_solver(engine, graph):
    students = _students(graph)
    s001 = students["S001"]
    thesis = engine.evaluate_thesis_eligibility(s001)
    assert thesis.status == DecisionStatus.NOT_ELIGIBLE
    assert thesis.horn_clause == HORN_PROGRAM.listing(["eligible_to_register/2"])
    assert thesis.proof["goal"] == "eligible_to_register(urdi:student/S001, urdi:course/THESIS)"
    assert thesis.proof["children"][0]["children"][-1]["goal"] == "3 >= 4"
    graduation = engine.evaluate_graduation(students["S003"])
    assert graduation.horn_clause.splitlines()[0] == "eligible_to_graduate(Student) :-"
    assert "urd:earnedCredits(Student, Total) :-" in graduation.horn_clause
    remaining = engine.get_remaining_credit_capacity(s001)
    assert remaining.facts["remaining_credits"] == 12
    assert remaining.proof["goal"] == "remaining_credits(urdi:student/S001, 12)"
    assert remaining.proof["reading"] == "山田太郎があと履修登録できる単位数は12"


# --- 判定はナレッジグラフ上の事実だけから行う ---------------------------------

def _outside_student() -> Student:
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


@pytest.mark.parametrize(
    "method",
    [
        "get_remaining_credit_capacity",
        "evaluate_required_courses",
        "evaluate_graduation",
        "evaluate_thesis_eligibility",
        "evaluate_first_year_requirement",
        "evaluate_personalized_notifications",
    ],
)
def test_student_outside_the_graph_is_not_judged(engine, method):
    with pytest.raises(ValueError, match="ナレッジグラフ上の学生ではないため判定できません: urdi:student/TEST-OK"):
        getattr(engine, method)(_outside_student())


def test_student_with_values_differing_from_the_graph_is_not_judged(engine, graph):
    s001 = _students(graph)["S001"]
    changed = s001.model_copy(update={"earned_credits": 124})
    with pytest.raises(ValueError, match="urdi:student/S001"):
        engine.evaluate_graduation(changed)
    # グラフの射影そのものなら判定できる
    assert engine.evaluate_graduation(s001).status == DecisionStatus.NOT_ELIGIBLE


def test_answer_for_a_student_outside_the_graph_is_cannot_judge(engine):
    service = AnswerService(engine, RuleBasedIntentDetector())
    intent, result = service.answer("卒業できますか？", _outside_student())
    assert intent == Intent.GRADUATION_STATUS
    assert result.status == DecisionStatus.UNKNOWN
    assert "判定できません" in result.message
    assert "urdi:student/TEST-OK" in result.facts["reason"]
