from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.models import Rule


class RuleValidationError(ValueError):
    """管理画面から保存できないルールであることを示す。"""


RULE_TYPE_LABELS = {
    "registration_deadline": "履修登録期限",
    "annual_credit_limit": "年間履修上限",
    "graduation_credit_requirement": "卒業必要単位数",
    "graduation_required_courses": "卒業必修科目",
    "thesis_eligibility": "卒業研究履修条件",
    "personalized_notification": "個別通知ルール",
}

SINGLETON_RULE_TYPES = set(RULE_TYPE_LABELS) - {"personalized_notification"}
STUDENT_CONDITION_FIELDS = {
    "grade": int,
    "earned_credits": int,
    "current_registered_credits": int,
    "required_a": bool,
    "required_b": bool,
    "registration_completed": bool,
}
CONDITION_OPERATORS = {"==", "!=", ">=", "<=", ">", "<"}


class RuleRepository:
    """rules.yamlを検証し、原子的に読み書きするCRUD境界。"""

    def __init__(self, rules_path: str | Path):
        self.path = Path(rules_path)

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
            normalized["minimum_grade"] = self._positive_int(normalized.get("minimum_grade"), "最低学年")
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
            if normalized["level"] not in {"info", "success", "warning", "error"}:
                raise RuleValidationError("通知レベルが正しくありません。")
            normalized["conditions"] = self._conditions(normalized.get("conditions"))

        try:
            return Rule.model_validate(normalized)
        except ValueError as exc:
            raise RuleValidationError(str(exc)) from exc

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

    @staticmethod
    def _required_courses(value: Any) -> list[str]:
        allowed = {"required_a", "required_b"}
        courses = list(value or [])
        if not courses or any(course not in allowed for course in courses):
            raise RuleValidationError("必修科目は必修Aまたは必修Bから1件以上選択してください。")
        return courses

    @staticmethod
    def _conditions(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise RuleValidationError("通知条件を1件以上追加してください。")
        conditions: list[dict[str, Any]] = []
        for row in value:
            if not isinstance(row, dict):
                raise RuleValidationError("通知条件の形式が正しくありません。")
            field = str(row.get("field", "")).strip()
            operator = str(row.get("operator", "")).strip()
            if field not in STUDENT_CONDITION_FIELDS:
                raise RuleValidationError(f"条件の学生属性が正しくありません: {field}")
            if operator not in CONDITION_OPERATORS:
                raise RuleValidationError(f"条件演算子が正しくありません: {operator}")
            expected_type = STUDENT_CONDITION_FIELDS[field]
            raw_value = row.get("value")
            if expected_type is bool:
                if operator not in {"==", "!="}:
                    raise RuleValidationError("はい／いいえ属性には == または != を使用してください。")
                if isinstance(raw_value, bool):
                    parsed_value = raw_value
                elif str(raw_value).strip().lower() in {"true", "はい", "1"}:
                    parsed_value = True
                elif str(raw_value).strip().lower() in {"false", "いいえ", "0"}:
                    parsed_value = False
                else:
                    raise RuleValidationError("条件値はtrue/falseまたははい/いいえで入力してください。")
            else:
                parsed_value = RuleRepository._nonnegative_int(raw_value, f"{field}の条件値")
            conditions.append({"field": field, "operator": operator, "value": parsed_value})
        return conditions

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
