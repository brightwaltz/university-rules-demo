from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Student(BaseModel):
    """判定処理が使う正規化済みの学生表現。

    ナレッジグラフ（schema:Person + ccso:UndergraduateStudent）から
    ``KnowledgeGraph.students()`` が射影して生成する。単位数は
    ccso:hasCompleted / ccso:hasRegistered をたどって schema:numberOfCredits を
    合計した導出値であり、グラフに直接書かれた数値ではない。
    """

    model_config = ConfigDict(extra="forbid")

    student_id: str
    name: str
    faculty: str
    year_of_study: int = Field(ge=1)
    earned_credits: int = Field(ge=0)
    current_registered_credits: int = Field(ge=0)
    completed_courses: list[str] = Field(default_factory=list)
    registered_courses: list[str] = Field(default_factory=list)
    registration_completed: bool
    iri: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.student_id} {self.name}"


class CourseInfo(BaseModel):
    """schema:Course ノードの射影。"""

    model_config = ConfigDict(extra="forbid")

    iri: str
    code: str
    name: str
    credits: int
    category: str | None = None


class LegislationRef(BaseModel):
    """schema:Legislation ノードの射影（条文単位）。"""

    model_config = ConfigDict(extra="forbid")

    iri: str
    identifier: str
    name: str
    parent_iri: str | None = None
    parent_name: str | None = None

    @property
    def citation(self) -> str:
        if self.parent_name:
            return f"{self.parent_name} {self.identifier}"
        return self.identifier


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
    legislation_iri: str | None = None
    legislation_identifier: str | None = None


class Premise(BaseModel):
    """判定の根拠となった原子論理式ひとつ分。

    Horn節の本体に現れる述語を、実際の値で具体化したもの。
    ``term`` はオントロジー上の用語（CURIE）を指す。
    """

    model_config = ConfigDict(extra="forbid")

    term: str
    label: str
    subject: str | None = None
    actual: Any = None
    comparator: str | None = None
    expected: Any = None
    satisfied: bool = True


class DecisionResult(BaseModel):
    status: DecisionStatus
    message: str
    rule_ids: list[str] = Field(default_factory=list)
    rule_references: list[RuleReference] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    calculation: str | None = None
    unmet_conditions: list[str] = Field(default_factory=list)
    ontology_terms: list[str] = Field(default_factory=list)
    horn_clause: str | None = None
    premises: list[Premise] = Field(default_factory=list)


class Notification(BaseModel):
    notification_id: str
    level: str
    title: str
    message: str
    decision: DecisionResult
