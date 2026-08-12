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
            {"field": "grade", "operator": ">=", "value": 4},
            {"field": "registration_completed", "operator": "==", "value": True},
        ],
    }


def test_rule_repository_create_update_delete(editable_rules_path):
    repository = RuleRepository(editable_rules_path)
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


def test_duplicate_rule_id_is_rejected_without_changing_file(editable_rules_path):
    repository = RuleRepository(editable_rules_path)
    before = editable_rules_path.read_text(encoding="utf-8")
    payload = personalized_payload("RULE-CREDIT-001")

    with pytest.raises(RuleValidationError, match="既に存在"):
        repository.create(payload)

    assert editable_rules_path.read_text(encoding="utf-8") == before


def test_personalized_rule_generates_notification(editable_rules_path, eligible_student):
    repository = RuleRepository(editable_rules_path)
    repository.create(personalized_payload())
    notices = NotificationService(RuleEngine(editable_rules_path)).generate(eligible_student)

    notice = next(item for item in notices if item.notification_id == "NOTICE-RULE-NOTICE-001")
    assert notice.message == "卒業準備を確認してください。"
    assert notice.decision.rule_ids == ["RULE-NOTICE-001"]


def test_deleting_required_rule_returns_unknown_instead_of_crashing(
    editable_rules_path, eligible_student
):
    repository = RuleRepository(editable_rules_path)
    repository.delete("RULE-CREDIT-001")
    engine = RuleEngine(editable_rules_path)
    service = AnswerService(engine, RuleBasedIntentDetector())

    _, result = service.answer("あと何単位履修できますか？", eligible_student)
    assert result.status == DecisionStatus.UNKNOWN
    assert "判定できません" in result.message
