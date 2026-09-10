from pathlib import Path

import pytest

from src.answer_service import AnswerService
from src.intent import RuleBasedIntentDetector
from src.models import DecisionStatus
from src.notification import NotificationService
from src.rule_engine import RuleEngine
from src.rule_repository import RuleRepository, RuleValidationError


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def editable_rules_path(tmp_path: Path) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text((ROOT / "data" / "rules.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return path


@pytest.fixture
def repository(editable_rules_path, graph) -> RuleRepository:
    return RuleRepository(editable_rules_path, graph)


def personalized_payload(rule_id: str = "RULE-NOTICE-001") -> dict:
    return {
        "rule_id": rule_id,
        "rule_type": "personalized_notification",
        "title": "4年生向け通知",
        "source": "研究用通知規程 第1条",
        "enabled": True,
        "message": "卒業準備を確認してください。",
        "level": "warning",
        "conditions": [
            {"term": "urd:yearOfStudy", "operator": ">=", "value": 4},
            {"term": "schema:actionStatus", "operator": "==", "value": True},
        ],
    }


def test_rule_repository_create_update_delete(repository):
    original_count = len(repository.list_rules())

    created = repository.create(personalized_payload())
    assert created.rule_id == "RULE-NOTICE-001"
    assert len(repository.list_rules()) == original_count + 1

    updated_payload = personalized_payload("RULE-NOTICE-001")
    updated_payload["message"] = "更新した通知です。"
    updated = repository.update("RULE-NOTICE-001", updated_payload)
    assert updated.message == "更新した通知です。"

    deleted = repository.delete("RULE-NOTICE-001")
    assert deleted.rule_id == "RULE-NOTICE-001"
    assert len(repository.list_rules()) == original_count


def test_duplicate_rule_id_is_rejected_without_changing_file(repository, editable_rules_path):
    before = editable_rules_path.read_text(encoding="utf-8")
    payload = personalized_payload("RULE-CREDIT-001")

    with pytest.raises(RuleValidationError, match="既に存在"):
        repository.create(payload)

    assert editable_rules_path.read_text(encoding="utf-8") == before


def test_personalized_rule_generates_notification(
    repository, editable_rules_path, graph, eligible_student
):
    repository.create(personalized_payload())
    notices = NotificationService(RuleEngine(editable_rules_path, graph)).generate(eligible_student)

    notice = next(item for item in notices if item.notification_id == "NOTICE-RULE-NOTICE-001")
    assert notice.message == "卒業準備を確認してください。"
    assert notice.decision.rule_ids == ["RULE-NOTICE-001"]


def test_deleting_required_rule_returns_unknown_instead_of_crashing(
    repository, editable_rules_path, graph, eligible_student
):
    repository.delete("RULE-CREDIT-001")
    engine = RuleEngine(editable_rules_path, graph)
    service = AnswerService(engine, RuleBasedIntentDetector())

    _, result = service.answer("あと何単位履修できますか？", eligible_student)
    assert result.status == DecisionStatus.UNKNOWN
    assert "判定できません" in result.message


def test_course_outside_the_graph_is_rejected(repository):
    repository.delete("RULE-GRAD-002")
    payload = {
        "rule_id": "RULE-GRAD-002",
        "rule_type": "graduation_required_courses",
        "title": "卒業必修科目",
        "source": "2026年度学則 第32条第2項",
        "subject": "urdi:program/engineering-2026",
        "legislation": "urdi:legislation/gakusoku-2026/art32-2",
        "required_courses": ["REQ-A", "NOT-IN-GRAPH"],
    }
    with pytest.raises(RuleValidationError, match="存在しない科目コード"):
        repository.create(payload)


def test_subject_class_must_match_the_ontology(repository):
    repository.delete("RULE-GRAD-001")
    payload = {
        "rule_id": "RULE-GRAD-001",
        "rule_type": "graduation_credit_requirement",
        "title": "卒業必要単位数",
        "source": "2026年度学則 第32条第1項",
        # 科目ノードは schema:EducationalOccupationalProgram ではない。
        "subject": "urdi:course/REQ-A",
        "legislation": "urdi:legislation/gakusoku-2026/art32-1",
        "required_credits": 124,
    }
    with pytest.raises(RuleValidationError, match="対象は schema:EducationalOccupationalProgram"):
        repository.create(payload)


def test_unknown_legislation_is_rejected(repository):
    repository.delete("RULE-CREDIT-001")
    payload = {
        "rule_id": "RULE-CREDIT-001",
        "rule_type": "annual_credit_limit",
        "title": "年間履修上限",
        "source": "2026年度履修規程 第12条",
        "subject": "urdi:program/engineering-2026",
        "legislation": "urdi:legislation/does-not-exist/art1",
        "max_credits": 48,
    }
    with pytest.raises(RuleValidationError, match="根拠条文がナレッジグラフにありません"):
        repository.create(payload)


def test_course_condition_uses_contains_operator(repository, editable_rules_path, graph):
    payload = personalized_payload("RULE-NOTICE-002")
    payload["conditions"] = [
        {"term": "ccso:hasCompleted", "operator": "not_contains", "value": "REQ-B"}
    ]
    payload["message"] = "必修Bが未修得です。"
    repository.create(payload)

    engine = RuleEngine(editable_rules_path, graph)
    students = {student.student_id: student for student in graph.students()}

    matched = engine.evaluate_personalized_notifications(students["S001"])
    assert [rule.rule_id for rule, _ in matched] == ["RULE-NOTICE-002"]

    assert engine.evaluate_personalized_notifications(students["S002"]) == []


def test_course_condition_rejects_scalar_operator(repository):
    payload = personalized_payload("RULE-NOTICE-003")
    payload["conditions"] = [
        {"term": "ccso:hasCompleted", "operator": ">=", "value": "REQ-B"}
    ]
    with pytest.raises(RuleValidationError, match="contains または not_contains"):
        repository.create(payload)
