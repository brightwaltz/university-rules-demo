from __future__ import annotations

import re
from enum import Enum
from typing import Protocol


class Intent(str, Enum):
    REGISTRATION_DEADLINE = "registration_deadline"
    REMAINING_CREDIT = "remaining_credit"
    CREDIT_LIMIT = "credit_limit"
    GRADUATION_STATUS = "graduation_status"
    GRADUATION_CREDIT_REQUIREMENT = "graduation_credit_requirement"
    REQUIRED_COURSES = "required_courses"
    THESIS_ELIGIBILITY = "thesis_eligibility"
    UNKNOWN = "unknown"


class IntentDetector(Protocol):
    def detect_intent(self, question: str) -> Intent: ...


class RuleBasedIntentDetector:
    """API不要の、説明可能なキーワード・正規表現ベース意図判定。"""

    def detect_intent(self, question: str) -> Intent:
        text = re.sub(r"[\s　、。？！?!]", "", question.lower())
        if not text:
            return Intent.UNKNOWN

        if "卒業研究" in text or "卒研" in text:
            return Intent.THESIS_ELIGIBILITY

        if ("履修登録" in text or "登録" in text) and any(
            word in text for word in ("いつまで", "期限", "締切", "しめきり")
        ):
            return Intent.REGISTRATION_DEADLINE

        if "必修" in text and any(
            word in text for word in ("足り", "修得", "取得", "大丈夫", "満た")
        ):
            return Intent.REQUIRED_COURSES

        if "卒業" in text and "単位" in text and any(
            word in text for word in ("必要", "何単位", "いくつ")
        ):
            return Intent.GRADUATION_CREDIT_REQUIREMENT

        if "卒業" in text and any(
            word in text for word in ("でき", "可能", "満た", "大丈夫", "条件")
        ):
            return Intent.GRADUATION_STATUS

        if "単位" in text and ("年間" in text or "上限" in text) and any(
            word in text for word in ("何", "まで", "上限")
        ):
            return Intent.CREDIT_LIMIT

        if ("単位" in text or "履修" in text) and any(
            word in text for word in ("あと", "残り", "とれる", "取れる", "履修でき", "まで")
        ):
            return Intent.REMAINING_CREDIT

        return Intent.UNKNOWN
