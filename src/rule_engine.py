from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.models import (
    DecisionResult,
    DecisionStatus,
    Rule,
    RuleReference,
    RulesConfig,
    Student,
)


class RuleEngine:
    """YAMLの規則値を、明示的なPython条件式で評価する決定論的エンジン。"""

    def __init__(self, rules_path: str | Path):
        raw = yaml.safe_load(Path(rules_path).read_text(encoding="utf-8"))
        self.config = RulesConfig(
            evaluation_date=raw["settings"]["evaluation_date"],
            rules=raw["rules"],
        )
        self._rules_by_type = {rule.rule_type: rule for rule in self.config.rules}

    @property
    def evaluation_date(self) -> date:
        return self.config.evaluation_date

    def _rule(self, rule_type: str) -> Rule:
        try:
            return self._rules_by_type[rule_type]
        except KeyError as exc:
            raise ValueError(f"必要なルールが未登録です: {rule_type}") from exc

    @staticmethod
    def _value(rule: Rule, key: str) -> Any:
        value = getattr(rule, key, None)
        if value is None:
            raise ValueError(f"{rule.rule_id} に {key} がありません")
        return value

    @staticmethod
    def _reference(rule: Rule) -> RuleReference:
        return RuleReference(rule_id=rule.rule_id, title=rule.title, source=rule.source)

    def get_registration_deadline(self) -> DecisionResult:
        rule = self._rule("registration_deadline")
        deadline = date.fromisoformat(str(self._value(rule, "deadline")))
        days_remaining = (deadline - self.evaluation_date).days
        return DecisionResult(
            status=DecisionStatus.INFO,
            message=f"履修登録期限は{deadline:%Y年%m月%d日}です。",
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={
                "academic_year": self._value(rule, "academic_year"),
                "semester": self._value(rule, "semester"),
                "deadline": deadline.isoformat(),
                "evaluation_date": self.evaluation_date.isoformat(),
                "days_remaining": days_remaining,
            },
            calculation=f"{deadline.isoformat()} - {self.evaluation_date.isoformat()} = {days_remaining}日",
        )

    def get_credit_limit(self) -> DecisionResult:
        rule = self._rule("annual_credit_limit")
        limit = int(self._value(rule, "max_credits"))
        return DecisionResult(
            status=DecisionStatus.INFO,
            message=f"年間履修上限は{limit}単位です。",
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={"annual_credit_limit": limit},
        )

    def get_remaining_credit_capacity(self, student: Student) -> DecisionResult:
        rule = self._rule("annual_credit_limit")
        limit = int(self._value(rule, "max_credits"))
        remaining = max(0, limit - student.current_registered_credits)
        return DecisionResult(
            status=DecisionStatus.INFO,
            message=f"現在、あと{remaining}単位履修できます。",
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={
                "annual_credit_limit": limit,
                "current_registered_credits": student.current_registered_credits,
                "remaining_credits": remaining,
            },
            calculation=f"max(0, {limit} - {student.current_registered_credits}) = {remaining}",
        )

    def get_graduation_credit_requirement(self) -> DecisionResult:
        rule = self._rule("graduation_credit_requirement")
        required = int(self._value(rule, "required_credits"))
        return DecisionResult(
            status=DecisionStatus.INFO,
            message=f"卒業に必要な単位数は{required}単位です。別途、必修科目要件もあります。",
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={"required_credits": required},
        )

    def evaluate_required_courses(self, student: Student) -> DecisionResult:
        rule = self._rule("graduation_required_courses")
        course_keys = list(self._value(rule, "required_courses"))
        labels = {"required_a": "必修A", "required_b": "必修B"}
        completion = {labels[key]: bool(getattr(student, key)) for key in course_keys}
        missing = [name for name, completed in completion.items() if not completed]
        eligible = not missing
        message = (
            "卒業に必要な必修科目をすべて修得しています。"
            if eligible
            else f"卒業に必要な必修科目が不足しています：{'、'.join(missing)}。"
        )
        return DecisionResult(
            status=DecisionStatus.ELIGIBLE if eligible else DecisionStatus.NOT_ELIGIBLE,
            message=message,
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={"required_courses": completion},
            unmet_conditions=[f"{name}を修得していない" for name in missing],
        )

    def evaluate_graduation(self, student: Student) -> DecisionResult:
        credit_rule = self._rule("graduation_credit_requirement")
        course_rule = self._rule("graduation_required_courses")
        required_credits = int(self._value(credit_rule, "required_credits"))
        course_keys = list(self._value(course_rule, "required_courses"))
        labels = {"required_a": "必修A", "required_b": "必修B"}

        unmet: list[str] = []
        if student.earned_credits < required_credits:
            shortage = required_credits - student.earned_credits
            unmet.append(f"卒業必要単位まで{shortage}単位不足")
        for key in course_keys:
            if not getattr(student, key):
                unmet.append(f"{labels[key]}を未修得")

        eligible = not unmet
        return DecisionResult(
            status=DecisionStatus.ELIGIBLE if eligible else DecisionStatus.NOT_ELIGIBLE,
            message=(
                "現時点の登録情報では卒業要件を満たしています。"
                if eligible
                else "現時点の登録情報では卒業要件を満たしていません。"
            ),
            rule_ids=[credit_rule.rule_id, course_rule.rule_id],
            rule_references=[self._reference(credit_rule), self._reference(course_rule)],
            facts={
                "required_credits": required_credits,
                "earned_credits": student.earned_credits,
                "required_a": student.required_a,
                "required_b": student.required_b,
            },
            calculation=f"単位判定: {student.earned_credits} >= {required_credits}",
            unmet_conditions=unmet,
        )

    def evaluate_thesis_eligibility(self, student: Student) -> DecisionResult:
        rule = self._rule("thesis_eligibility")
        minimum_grade = int(self._value(rule, "minimum_grade"))
        minimum_credits = int(self._value(rule, "minimum_earned_credits"))
        course_keys = list(self._value(rule, "required_courses"))
        labels = {"required_a": "必修A", "required_b": "必修B"}

        unmet: list[str] = []
        if student.grade < minimum_grade:
            unmet.append(f"{minimum_grade}年生以上ではない")
        if student.earned_credits < minimum_credits:
            unmet.append(f"修得単位が{minimum_credits}単位未満")
        for key in course_keys:
            if not getattr(student, key):
                unmet.append(f"{labels[key]}を未修得")

        eligible = not unmet
        return DecisionResult(
            status=DecisionStatus.ELIGIBLE if eligible else DecisionStatus.NOT_ELIGIBLE,
            message=(
                "卒業研究を履修できます。"
                if eligible
                else f"卒業研究を履修できません。不足条件：{'、'.join(unmet)}。"
            ),
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={
                "grade": student.grade,
                "minimum_grade": minimum_grade,
                "earned_credits": student.earned_credits,
                "minimum_earned_credits": minimum_credits,
                "required_a": student.required_a,
                "required_b": student.required_b,
            },
            unmet_conditions=unmet,
        )

    def evaluate_personalized_notifications(
        self, student: Student
    ) -> list[tuple[Rule, DecisionResult]]:
        """管理画面で追加された通知条件を固定演算子で評価する。"""
        results: list[tuple[Rule, DecisionResult]] = []
        operators = {
            "==": lambda actual, expected: actual == expected,
            "!=": lambda actual, expected: actual != expected,
            ">=": lambda actual, expected: actual >= expected,
            "<=": lambda actual, expected: actual <= expected,
            ">": lambda actual, expected: actual > expected,
            "<": lambda actual, expected: actual < expected,
        }
        for rule in self.config.rules:
            if rule.rule_type != "personalized_notification" or not getattr(rule, "enabled", True):
                continue
            conditions = list(self._value(rule, "conditions"))
            facts: dict[str, Any] = {}
            matched = True
            for condition in conditions:
                field = condition["field"]
                operator = condition["operator"]
                expected = condition["value"]
                actual = getattr(student, field)
                facts[field] = {
                    "actual": actual,
                    "operator": operator,
                    "expected": expected,
                }
                if operator not in operators or not operators[operator](actual, expected):
                    matched = False
            if matched:
                results.append(
                    (
                        rule,
                        DecisionResult(
                            status=DecisionStatus.INFO,
                            message=str(self._value(rule, "message")),
                            rule_ids=[rule.rule_id],
                            rule_references=[self._reference(rule)],
                            facts=facts,
                            calculation=" AND ".join(
                                f"{field} {values['operator']} {values['expected']}"
                                for field, values in facts.items()
                            ),
                        ),
                    )
                )
        return results
