from __future__ import annotations

from src.intent import Intent, IntentDetector
from src.models import DecisionResult, DecisionStatus, Student
from src.rule_engine import RuleEngine


class AnswerService:
    """IntentをRule Engineへルーティングし、構造化結果のみを回答に使う。"""

    def __init__(self, rule_engine: RuleEngine, intent_detector: IntentDetector):
        self.rule_engine = rule_engine
        self.intent_detector = intent_detector

    def answer(self, question: str, student: Student) -> tuple[Intent, DecisionResult]:
        intent = self.intent_detector.detect_intent(question)
        handlers = {
            Intent.REGISTRATION_DEADLINE: self.rule_engine.get_registration_deadline,
            Intent.REMAINING_CREDIT: lambda: self.rule_engine.get_remaining_credit_capacity(student),
            Intent.CREDIT_LIMIT: self.rule_engine.get_credit_limit,
            Intent.GRADUATION_STATUS: lambda: self.rule_engine.evaluate_graduation(student),
            Intent.GRADUATION_CREDIT_REQUIREMENT: self.rule_engine.get_graduation_credit_requirement,
            Intent.REQUIRED_COURSES: lambda: self.rule_engine.evaluate_required_courses(student),
            Intent.THESIS_ELIGIBILITY: lambda: self.rule_engine.evaluate_thesis_eligibility(student),
        }
        handler = handlers.get(intent)
        if handler is None:
            return intent, DecisionResult(
                status=DecisionStatus.UNKNOWN,
                message="このプロトタイプに登録されているルールでは判定できません。",
                facts={"question": question, "detected_intent": Intent.UNKNOWN.value},
            )
        try:
            return intent, handler()
        except ValueError as exc:
            return intent, DecisionResult(
                status=DecisionStatus.UNKNOWN,
                message="このプロトタイプに登録されているルールでは判定できません。",
                facts={
                    "question": question,
                    "detected_intent": intent.value,
                    "reason": str(exc),
                },
            )
