from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Student(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: str
    name: str
    faculty: str
    grade: int = Field(ge=1)
    earned_credits: int = Field(ge=0)
    current_registered_credits: int = Field(ge=0)
    required_a: bool
    required_b: bool
    registration_completed: bool

    @property
    def display_name(self) -> str:
        return f"{self.student_id} {self.name}"


class Rule(BaseModel):
    model_config = ConfigDict(extra="allow")

    rule_id: str
    rule_type: str
    title: str
    source: str


class RulesConfig(BaseModel):
    evaluation_date: date
    rules: list[Rule]


class DecisionStatus(str, Enum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    INFO = "info"
    UNKNOWN = "unknown"


class RuleReference(BaseModel):
    rule_id: str
    title: str
    source: str


class DecisionResult(BaseModel):
    status: DecisionStatus
    message: str
    rule_ids: list[str] = Field(default_factory=list)
    rule_references: list[RuleReference] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    calculation: str | None = None
    unmet_conditions: list[str] = Field(default_factory=list)


class Notification(BaseModel):
    notification_id: str
    level: str
    title: str
    message: str
    decision: DecisionResult
