from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.knowledge_graph import KnowledgeGraph
from src.models import Rule
from src.ontology import (
    CONDITION_TERMS,
    RULE_TYPE_LABELS,
    RULE_TYPE_ONTOLOGY,
    SINGLETON_RULE_TYPES,
)


class RuleValidationError(ValueError):
    """管理画面から保存できないルールであることを示す。"""


SCALAR_OPERATORS = {"==", "!=", ">=", "<=", ">", "<"}
BOOLEAN_OPERATORS = {"==", "!="}
LIST_OPERATORS = {"contains", "not_contains"}
NOTIFICATION_LEVELS = {"info", "success", "warning", "error"}

#: 個別通知ルール以外は、根拠となる条文と対象ノードの指定を必須にする。
LEGISLATION_REQUIRED_TYPES = SINGLETON_RULE_TYPES


class RuleRepository:
    """rules.yamlを検証し、原子的に読み書きするCRUD境界。

    検証はナレッジグラフに対して行う。対象ノードの型が
    ``RULE_TYPE_ONTOLOGY[rule_type].subject_class`` と一致すること、
    必修科目がグラフの科目カタログに存在すること、根拠がグラフ上の
    schema:Legislation の条文であることを保存前に確認する。
    """

    def __init__(self, rules_path: str | Path, graph: KnowledgeGraph):
        self.path = Path(rules_path)
        self.graph = graph

    def _load_document(self) -> dict[str, Any]:
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
            raise RuleValidationError("rules.yamlの形式が正しくありません。")
        if not isinstance(raw.get("settings"), dict):
            raise RuleValidationError("settingsがありません。")
        return raw

    def list_rules(self) -> list[Rule]:
        return [Rule.model_validate(item) for item in self._load_document()["rules"]]

    def get(self, rule_id: str) -> Rule:
        for rule in self.list_rules():
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(rule_id)

    def create(self, payload: dict[str, Any]) -> Rule:
        document = self._load_document()
        existing = [Rule.model_validate(item) for item in document["rules"]]
        rule = self._validate(payload, existing=existing)
        document["rules"].append(rule.model_dump(mode="python", exclude_none=True))
        self._save_document(document)
        return rule

    def update(self, original_rule_id: str, payload: dict[str, Any]) -> Rule:
        document = self._load_document()
        index = next(
            (i for i, item in enumerate(document["rules"]) if item.get("rule_id") == original_rule_id),
            None,
        )
        if index is None:
            raise KeyError(original_rule_id)
        existing = [
            Rule.model_validate(item)
            for i, item in enumerate(document["rules"])
            if i != index
        ]
        rule = self._validate(payload, existing=existing)
        document["rules"][index] = rule.model_dump(mode="python", exclude_none=True)
        self._save_document(document)
        return rule

    def delete(self, rule_id: str) -> Rule:
        document = self._load_document()
        index = next(
            (i for i, item in enumerate(document["rules"]) if item.get("rule_id") == rule_id),
            None,
        )
        if index is None:
            raise KeyError(rule_id)
        removed = Rule.model_validate(document["rules"].pop(index))
        self._save_document(document)
        return removed

    def _validate(self, payload: dict[str, Any], existing: list[Rule]) -> Rule:
        normalized = dict(payload)
        for key in ("rule_id", "rule_type", "title", "source"):
            value = str(normalized.get(key, "")).strip()
            if not value:
                raise RuleValidationError(f"{key}は必須です。")
            normalized[key] = value

        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", normalized["rule_id"]):
            raise RuleValidationError("ルールIDは半角英大文字・数字・ハイフン・アンダースコアで入力してください。")
        if normalized["rule_type"] not in RULE_TYPE_LABELS:
            raise RuleValidationError("未対応のルール種別です。")
        if any(rule.rule_id == normalized["rule_id"] for rule in existing):
            raise RuleValidationError(f"ルールID {normalized['rule_id']} は既に存在します。")
        if normalized["rule_type"] in SINGLETON_RULE_TYPES and any(
            rule.rule_type == normalized["rule_type"] for rule in existing
        ):
            raise RuleValidationError("このルール種別は1件だけ登録できます。既存ルールを編集してください。")

        rule_type = normalized["rule_type"]
        if rule_type in LEGISLATION_REQUIRED_TYPES:
            normalized["subject"] = self._subject(normalized.get("subject"), rule_type)
            normalized["legislation"] = self._legislation(normalized.get("legislation"))

        if rule_type == "registration_deadline":
            normalized["academic_year"] = self._positive_int(normalized.get("academic_year"), "年度")
            normalized["semester"] = str(normalized.get("semester", "")).strip()
            if normalized["semester"] not in {"first", "second"}:
                raise RuleValidationError("学期はfirstまたはsecondを選択してください。")
            try:
                normalized["deadline"] = date.fromisoformat(str(normalized.get("deadline"))).isoformat()
            except ValueError as exc:
                raise RuleValidationError("期限は有効な日付にしてください。") from exc
        elif rule_type == "annual_credit_limit":
            normalized["max_credits"] = self._positive_int(normalized.get("max_credits"), "年間履修上限")
        elif rule_type == "graduation_credit_requirement":
            normalized["required_credits"] = self._positive_int(normalized.get("required_credits"), "卒業必要単位数")
        elif rule_type == "graduation_required_courses":
            normalized["required_courses"] = self._required_courses(normalized.get("required_courses"))
        elif rule_type == "thesis_eligibility":
            normalized["minimum_year_of_study"] = self._positive_int(
                normalized.get("minimum_year_of_study"), "最低学年"
            )
            normalized["minimum_earned_credits"] = self._nonnegative_int(
                normalized.get("minimum_earned_credits"), "最低修得単位数"
            )
            normalized["required_courses"] = self._required_courses(normalized.get("required_courses"))
        elif rule_type == "personalized_notification":
            normalized["enabled"] = bool(normalized.get("enabled", True))
            normalized["message"] = str(normalized.get("message", "")).strip()
            if not normalized["message"]:
                raise RuleValidationError("通知本文は必須です。")
            normalized["level"] = str(normalized.get("level", "info"))
            if normalized["level"] not in NOTIFICATION_LEVELS:
                raise RuleValidationError("通知レベルが正しくありません。")
            normalized["conditions"] = self._conditions(normalized.get("conditions"))

        try:
            return Rule.model_validate(normalized)
        except ValueError as exc:
            raise RuleValidationError(str(exc)) from exc

    # --- グラフに対する検証 -----------------------------------------------

    def _subject(self, value: Any, rule_type: str) -> str:
        iri = str(value or "").strip()
        if not iri:
            raise RuleValidationError("対象ノード（subject）は必須です。")
        if not self.graph.has_node(iri):
            raise RuleValidationError(f"対象ノードがナレッジグラフにありません: {iri}")
        expected_class = RULE_TYPE_ONTOLOGY[rule_type].subject_class
        if expected_class not in self.graph.types_of(self.graph.node(iri)):
            raise RuleValidationError(
                f"このルール種別の対象は {expected_class} である必要があります: {iri}"
            )
        return iri

    def _legislation(self, value: Any) -> str:
        iri = str(value or "").strip()
        if not iri:
            raise RuleValidationError("根拠条文（legislation）は必須です。")
        reference = self.graph.legislation(iri)
        if reference is None:
            raise RuleValidationError(f"根拠条文がナレッジグラフにありません: {iri}")
        return iri

    def _required_courses(self, value: Any) -> list[str]:
        courses = [str(code).strip() for code in (value or []) if str(code).strip()]
        if not courses:
            raise RuleValidationError("必修科目を1件以上選択してください。")
        unknown = [code for code in courses if not self.graph.has_course(code)]
        if unknown:
            raise RuleValidationError(
                f"ナレッジグラフに存在しない科目コードです: {'、'.join(unknown)}"
            )
        if len(set(courses)) != len(courses):
            raise RuleValidationError("同じ科目が重複しています。")
        return courses

    def _conditions(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise RuleValidationError("通知条件を1件以上追加してください。")
        conditions: list[dict[str, Any]] = []
        for row in value:
            if not isinstance(row, dict):
                raise RuleValidationError("通知条件の形式が正しくありません。")
            term = str(row.get("term", "")).strip()
            operator = str(row.get("operator", "")).strip()
            if term not in CONDITION_TERMS:
                raise RuleValidationError(f"条件の用語が正しくありません: {term}")
            _, expected_type = CONDITION_TERMS[term]
            raw_value = row.get("value")

            if expected_type is bool:
                if operator not in BOOLEAN_OPERATORS:
                    raise RuleValidationError("はい／いいえの用語には == または != を使用してください。")
                parsed_value = self._boolean(raw_value)
            elif expected_type is list:
                if operator not in LIST_OPERATORS:
                    raise RuleValidationError(
                        "科目の用語には contains または not_contains を使用してください。"
                    )
                parsed_value = str(raw_value or "").strip()
                if not self.graph.has_course(parsed_value):
                    raise RuleValidationError(
                        f"ナレッジグラフに存在しない科目コードです: {parsed_value}"
                    )
            else:
                if operator not in SCALAR_OPERATORS:
                    raise RuleValidationError(f"条件演算子が正しくありません: {operator}")
                parsed_value = self._nonnegative_int(raw_value, f"{term}の条件値")

            conditions.append({"term": term, "operator": operator, "value": parsed_value})
        return conditions

    # --- 値の正規化 -------------------------------------------------------

    @staticmethod
    def _boolean(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "はい", "1"}:
            return True
        if text in {"false", "いいえ", "0"}:
            return False
        raise RuleValidationError("条件値はtrue/falseまたははい/いいえで入力してください。")

    @staticmethod
    def _positive_int(value: Any, label: str) -> int:
        number = RuleRepository._nonnegative_int(value, label)
        if number == 0:
            raise RuleValidationError(f"{label}は1以上にしてください。")
        return number

    @staticmethod
    def _nonnegative_int(value: Any, label: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise RuleValidationError(f"{label}は整数で入力してください。") from exc
        if number < 0:
            raise RuleValidationError(f"{label}は0以上にしてください。")
        return number

    def _save_document(self, document: dict[str, Any]) -> None:
        content = yaml.safe_dump(
            document,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
