from src.answer_service import AnswerService
from src.intent import Intent, RuleBasedIntentDetector
from src.models import DecisionStatus


def test_unknown_question_returns_unknown_without_rule(engine, eligible_student):
    service = AnswerService(engine, RuleBasedIntentDetector())
    intent, result = service.answer("学食のおすすめは？", eligible_student)
    assert intent == Intent.UNKNOWN
    assert result.status == DecisionStatus.UNKNOWN
    assert result.rule_ids == []
    assert result.message == "このプロトタイプに登録されているルールでは判定できません。"


def test_expression_variations_are_detected():
    detector = RuleBasedIntentDetector()
    assert detector.detect_intent("あと何単位とれる？") == Intent.REMAINING_CREDIT
    assert detector.detect_intent("卒業条件大丈夫？") == Intent.GRADUATION_STATUS
    assert detector.detect_intent("履修登録はいつまで？") == Intent.REGISTRATION_DEADLINE
    assert detector.detect_intent("年間何単位まで履修できますか？") == Intent.CREDIT_LIMIT
