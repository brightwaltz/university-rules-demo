from src.answer_service import AnswerService
from src.intent import Intent, RuleBasedIntentDetector
from src.models import DecisionStatus


def test_unknown_question_returns_unknown_without_rule(eligible_case):
    engine, student = eligible_case
    service = AnswerService(engine, RuleBasedIntentDetector())
    intent, result = service.answer("学食のおすすめは？", student)
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


def test_first_year_requirement_intent():
    detector = RuleBasedIntentDetector()
    for question in ("1年次の単位要件は満たしていますか？", "1年次で何単位必要？", "修学指導の対象ですか？"):
        assert detector.detect_intent(question) == Intent.FIRST_YEAR_REQUIREMENT, question
    # 既存の質問の判定は変わらない
    for question, intent in (
        ("履修登録はいつまで？", Intent.REGISTRATION_DEADLINE),
        ("あと何単位履修できますか？", Intent.REMAINING_CREDIT),
        ("年間何単位まで履修できますか？", Intent.CREDIT_LIMIT),
        ("私は卒業要件を満たしていますか？", Intent.GRADUATION_STATUS),
        ("卒業に必要な単位数は？", Intent.GRADUATION_CREDIT_REQUIREMENT),
        ("必修科目は足りていますか？", Intent.REQUIRED_COURSES),
        ("卒業研究を履修できますか？", Intent.THESIS_ELIGIBILITY),
        ("学食のおすすめは？", Intent.UNKNOWN),
    ):
        assert detector.detect_intent(question) == intent, question


def test_first_year_question_is_answered(engine, graph):
    service = AnswerService(engine, RuleBasedIntentDetector())
    student = next(s for s in graph.students() if s.student_id == "S002")
    intent, result = service.answer("修学指導の対象ですか？", student)
    assert intent == Intent.FIRST_YEAR_REQUIREMENT
    assert result.status == DecisionStatus.NOT_ELIGIBLE
    assert result.unmet_conditions == ["1年次の修得単位が3単位不足"]
