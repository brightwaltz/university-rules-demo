from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.knowledge_graph import KnowledgeGraph
from src.models import (
    DecisionResult,
    DecisionStatus,
    Premise,
    Rule,
    RuleReference,
    RulesConfig,
    Student,
)
from src.ontology import CONDITION_TERM_LABELS, CONDITION_TERMS, RULE_TYPE_ONTOLOGY


class RuleEngine:
    """YAMLの規則値を、明示的なPython条件式で評価する決定論的エンジン。

    各判定は「どのオントロジー用語を読んだか」（ontology_terms）と、
    「Horn節の本体をどう具体化したか」（premises）を結果へ添える。
    生成AIは介在せず、可否・数値・根拠はすべてこのクラスが決める。
    """

    def __init__(self, rules_path: str | Path, graph: KnowledgeGraph):
        raw = yaml.safe_load(Path(rules_path).read_text(encoding="utf-8"))
        self.config = RulesConfig(
            evaluation_date=raw["settings"]["evaluation_date"],
            rules=raw["rules"],
        )
        self.graph = graph
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

    def _reference(self, rule: Rule) -> RuleReference:
        legislation = self.graph.legislation(getattr(rule, "legislation", None))
        return RuleReference(
            rule_id=rule.rule_id,
            title=rule.title,
            source=rule.source,
            legislation_iri=legislation.iri if legislation else None,
            legislation_identifier=legislation.citation if legislation else None,
        )

    @staticmethod
    def _ontology(rule: Rule) -> Any:
        return RULE_TYPE_ONTOLOGY[rule.rule_type]

    def _subject(self, rule: Rule) -> str | None:
        subject = getattr(rule, "subject", None)
        return str(subject) if subject else None

    def _course_premises(
        self, student: Student, course_codes: list[str], term: str
    ) -> tuple[list[Premise], list[str]]:
        """必修・前提科目の修得状況を原子論理式へ落とす。"""
        premises: list[Premise] = []
        missing: list[str] = []
        for code in course_codes:
            label = self.graph.course_label(code)
            satisfied = code in student.completed_courses
            premises.append(
                Premise(
                    term=term,
                    label=f"{label}を修得している",
                    subject=student.iri,
                    actual=satisfied,
                    comparator="contains",
                    expected=code,
                    satisfied=satisfied,
                )
            )
            if not satisfied:
                missing.append(label)
        return premises, missing

    def get_registration_deadline(self) -> DecisionResult:
        rule = self._rule("registration_deadline")
        ontology = self._ontology(rule)
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
            ontology_terms=ontology.terms(),
            horn_clause=ontology.horn_clause,
            premises=[
                Premise(
                    term="schema:applicationDeadline",
                    label="履修登録期限",
                    subject=self._subject(rule),
                    actual=deadline.isoformat(),
                ),
                Premise(
                    term="urd:evaluationDate",
                    label="判定基準日",
                    actual=self.evaluation_date.isoformat(),
                ),
            ],
        )

    def get_credit_limit(self) -> DecisionResult:
        rule = self._rule("annual_credit_limit")
        ontology = self._ontology(rule)
        limit = int(self._value(rule, "max_credits"))
        return DecisionResult(
            status=DecisionStatus.INFO,
            message=f"年間履修上限は{limit}単位です。",
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={"annual_credit_limit": limit},
            ontology_terms=ontology.terms(),
            horn_clause=ontology.horn_clause,
            premises=[
                Premise(
                    term="urd:annualCreditLimit",
                    label="年間履修上限",
                    subject=self._subject(rule),
                    actual=limit,
                )
            ],
        )

    def get_remaining_credit_capacity(self, student: Student) -> DecisionResult:
        rule = self._rule("annual_credit_limit")
        ontology = self._ontology(rule)
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
                "registered_courses": student.registered_courses,
            },
            calculation=f"max(0, {limit} - {student.current_registered_credits}) = {remaining}",
            ontology_terms=ontology.terms(),
            horn_clause=ontology.horn_clause,
            premises=[
                Premise(
                    term="urd:annualCreditLimit",
                    label="年間履修上限",
                    subject=self._subject(rule),
                    actual=limit,
                ),
                Premise(
                    term="urd:registeredCredits",
                    label="履修登録単位数（ccso:hasRegistered の schema:numberOfCredits 合計）",
                    subject=student.iri,
                    actual=student.current_registered_credits,
                ),
            ],
        )

    def get_graduation_credit_requirement(self) -> DecisionResult:
        rule = self._rule("graduation_credit_requirement")
        ontology = self._ontology(rule)
        required = int(self._value(rule, "required_credits"))
        return DecisionResult(
            status=DecisionStatus.INFO,
            message=f"卒業に必要な単位数は{required}単位です。別途、必修科目要件もあります。",
            rule_ids=[rule.rule_id],
            rule_references=[self._reference(rule)],
            facts={"required_credits": required},
            ontology_terms=ontology.terms(),
            horn_clause=ontology.horn_clause,
            premises=[
                Premise(
                    term="schema:numberOfCredits",
                    label="卒業に必要な単位数",
                    subject=self._subject(rule),
                    actual=required,
                )
            ],
        )

    def evaluate_required_courses(self, student: Student) -> DecisionResult:
        rule = self._rule("graduation_required_courses")
        ontology = self._ontology(rule)
        course_codes = [str(code) for code in self._value(rule, "required_courses")]
        premises, missing = self._course_premises(student, course_codes, "ccso:hasCompleted")
        completion = {
            self.graph.course_label(code): code in student.completed_courses
            for code in course_codes
        }
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
            ontology_terms=ontology.terms(),
            horn_clause=ontology.horn_clause,
            premises=premises,
        )

    def evaluate_graduation(self, student: Student) -> DecisionResult:
        credit_rule = self._rule("graduation_credit_requirement")
        course_rule = self._rule("graduation_required_courses")
        required_credits = int(self._value(credit_rule, "required_credits"))
        course_codes = [str(code) for code in self._value(course_rule, "required_courses")]

        unmet: list[str] = []
        credits_satisfied = student.earned_credits >= required_credits
        if not credits_satisfied:
            shortage = required_credits - student.earned_credits
            unmet.append(f"卒業必要単位まで{shortage}単位不足")

        course_premises, missing = self._course_premises(
            student, course_codes, "ccso:hasCompleted"
        )
        unmet.extend(f"{name}を未修得" for name in missing)

        premises = [
            Premise(
                term="urd:earnedCredits",
                label="修得単位数（ccso:hasCompleted の schema:numberOfCredits 合計）",
                subject=student.iri,
                actual=student.earned_credits,
                comparator=">=",
                expected=required_credits,
                satisfied=credits_satisfied,
            ),
            *course_premises,
        ]

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
                "required_courses": {
                    self.graph.course_label(code): code in student.completed_courses
                    for code in course_codes
                },
            },
            calculation=f"単位判定: {student.earned_credits} >= {required_credits}",
            unmet_conditions=unmet,
            ontology_terms=sorted(
                {
                    *RULE_TYPE_ONTOLOGY["graduation_credit_requirement"].terms(),
                    *RULE_TYPE_ONTOLOGY["graduation_required_courses"].terms(),
                }
            ),
            horn_clause=(
                "eligible_to_graduate(Student) :- "
                "meets_credit_requirement(Student), meets_course_requirement(Student)."
            ),
            premises=premises,
        )

    def evaluate_thesis_eligibility(self, student: Student) -> DecisionResult:
        rule = self._rule("thesis_eligibility")
        ontology = self._ontology(rule)
        minimum_year = int(self._value(rule, "minimum_year_of_study"))
        minimum_credits = int(self._value(rule, "minimum_earned_credits"))
        course_codes = [str(code) for code in self._value(rule, "required_courses")]

        unmet: list[str] = []
        year_satisfied = student.year_of_study >= minimum_year
        if not year_satisfied:
            unmet.append(f"{minimum_year}年生以上ではない")
        credits_satisfied = student.earned_credits >= minimum_credits
        if not credits_satisfied:
            unmet.append(f"修得単位が{minimum_credits}単位未満")

        course_premises, missing = self._course_premises(
            student, course_codes, "ccso:hasCompleted"
        )
        unmet.extend(f"{name}を未修得" for name in missing)

        premises = [
            Premise(
                term="urd:yearOfStudy",
                label="学年",
                subject=student.iri,
                actual=student.year_of_study,
                comparator=">=",
                expected=minimum_year,
                satisfied=year_satisfied,
            ),
            Premise(
                term="urd:earnedCredits",
                label="修得単位数",
                subject=student.iri,
                actual=student.earned_credits,
                comparator=">=",
                expected=minimum_credits,
                satisfied=credits_satisfied,
            ),
            *course_premises,
        ]

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
                "year_of_study": student.year_of_study,
                "minimum_year_of_study": minimum_year,
                "earned_credits": student.earned_credits,
                "minimum_earned_credits": minimum_credits,
                "prerequisite_courses": {
                    self.graph.course_label(code): code in student.completed_courses
                    for code in course_codes
                },
            },
            unmet_conditions=unmet,
            ontology_terms=ontology.terms(),
            horn_clause=ontology.horn_clause,
            premises=premises,
        )

    def evaluate_personalized_notifications(
        self, student: Student
    ) -> list[tuple[Rule, DecisionResult]]:
        """管理画面で追加された通知条件を固定演算子で評価する。"""
        results: list[tuple[Rule, DecisionResult]] = []
        scalar_operators = {
            "==": lambda actual, expected: actual == expected,
            "!=": lambda actual, expected: actual != expected,
            ">=": lambda actual, expected: actual >= expected,
            "<=": lambda actual, expected: actual <= expected,
            ">": lambda actual, expected: actual > expected,
            "<": lambda actual, expected: actual < expected,
        }
        list_operators = {
            "contains": lambda actual, expected: expected in actual,
            "not_contains": lambda actual, expected: expected not in actual,
        }
        ontology = RULE_TYPE_ONTOLOGY["personalized_notification"]

        for rule in self.config.rules:
            if rule.rule_type != "personalized_notification" or not getattr(rule, "enabled", True):
                continue
            conditions = list(self._value(rule, "conditions"))
            facts: dict[str, Any] = {}
            premises: list[Premise] = []
            matched = True
            for condition in conditions:
                term = condition["term"]
                operator = condition["operator"]
                expected = condition["value"]
                attribute, expected_type = CONDITION_TERMS[term]
                actual = getattr(student, attribute)
                if expected_type is list:
                    satisfied = operator in list_operators and list_operators[operator](
                        actual, expected
                    )
                    displayed_actual = [
                        self.graph.course_label(code) for code in actual
                    ]
                else:
                    satisfied = operator in scalar_operators and scalar_operators[operator](
                        actual, expected
                    )
                    displayed_actual = actual
                facts[term] = {
                    "actual": displayed_actual,
                    "operator": operator,
                    "expected": expected,
                }
                premises.append(
                    Premise(
                        term=term,
                        label=CONDITION_TERM_LABELS.get(term, term),
                        subject=student.iri,
                        actual=displayed_actual,
                        comparator=operator,
                        expected=expected,
                        satisfied=satisfied,
                    )
                )
                if not satisfied:
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
                                f"{term} {values['operator']} {values['expected']}"
                                for term, values in facts.items()
                            ),
                            ontology_terms=sorted({*ontology.terms(), *facts}),
                            horn_clause=ontology.horn_clause,
                            premises=premises,
                        ),
                    )
                )
        return results
