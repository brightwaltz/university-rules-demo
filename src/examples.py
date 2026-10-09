"""例題チェック：仮想の学生と、規程から期待される結論の組を、システムの判定と照合する。

例題（``data/examples.yaml``）は、AI が規程の本文から作ったもの（origin: ai）、
境界値を機械的に生成したもの（origin: boundary）、担当者が足したもの（origin: human）の3種。
業務担当者は Horn 節を読まずに「状況」と「結論」だけを見て、業務として正しいかを判断する。
担当者が確認した例題は、以後の自動テストでシステムの判定と照合される（回帰テスト）。

判定は、例題の学生をナレッジグラフへ足したグラフの上で RuleEngine が行う。
学生の値を Student に直接書き込むのではなく、修得の記録（schema:AchieveAction）と
履修登録（schema:RegisterAction）として事実を置き、そこから射影・判定する。

``data/examples.yaml`` は常に機械が書く（``write_examples``）。生成スクリプトと画面の保存が
同じ書き出し関数を使うので、``scripts/generate_examples.py --check`` で差分を検出できる。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from src.knowledge_graph import KnowledgeGraph
from src.models import DecisionStatus
from src.rule_engine import RuleEngine


class ExampleValidationError(ValueError):
    """examples.yaml の内容が例題として成り立たないことを示す。"""


#: 例題の rule_type → (画面での表示名, RuleEngine のメソッド名, 本文を示す rule_type)
EXAMPLE_RULE_TYPES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "first_year_credit_requirement": (
        "1年次の修得単位要件（履修規程 第14条）",
        "evaluate_first_year_requirement",
        ("first_year_credit_requirement",),
    ),
    "thesis_eligibility": (
        "卒業研究の履修条件（工学部履修細則 第18条）",
        "evaluate_thesis_eligibility",
        ("thesis_eligibility",),
    ),
    "graduation": (
        "卒業判定（学則 第32条）",
        "evaluate_graduation",
        ("graduation_credit_requirement", "graduation_required_courses"),
    ),
    "graduation_required_courses": (
        "卒業必修科目（学則 第32条第2項）",
        "evaluate_required_courses",
        ("graduation_required_courses",),
    ),
}
EXAMPLE_RULE_LABELS = {key: value[0] for key, value in EXAMPLE_RULE_TYPES.items()}

ORIGINS = ("ai", "human", "boundary")
EXPECTED_STATUSES = ("eligible", "not_eligible", "unknown")
VERDICTS = ("unreviewed", "agree", "disagree", "unsure")

EXAMPLES_HEADER = (
    "# 例題：仮想の学生と、規程から期待される結論の組。\n"
    "# origin: boundary（境界値を機械的に生成）/ ai（AI が規程の本文を読んで作成）/ human（担当者が追加）\n"
    "# review.verdict: unreviewed / agree（AI の結論は正しい）/ disagree（正しくない。correct_status が正解）"
    "/ unsure（判断できない）\n"
    "# このファイルは scripts/generate_examples.py と画面の「確認結果を保存」が書き出す。\n"
)

#: 卒業必修などの「必修科目」区分（describe_student で科目名を括弧に出す）
REQUIRED_CATEGORY = "urdi:term/required"

#: 例題の履修登録は、基準日の学期（2026年度前期）の登録として置く
REGISTRATION_ACADEMIC_YEAR = "2026"
REGISTRATION_SEMESTER = "first"


def empty_review() -> dict[str, Any]:
    return {"verdict": "unreviewed", "correct_status": None, "note": None, "reviewer": None, "reviewed_on": None}


# --- 書き出し ---------------------------------------------------------------


def _ordered(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """origin が ai/human の例題（既存の順）→ boundary の例題（生成順）。"""
    return [item for item in examples if item.get("origin") != "boundary"] + [
        item for item in examples if item.get("origin") == "boundary"
    ]


def dump_examples(examples: list[dict[str, Any]]) -> str:
    """examples.yaml の全文。生成スクリプトと ExampleRepository の保存が共通で使う。"""
    body = yaml.safe_dump(
        {"examples": _ordered(examples)}, allow_unicode=True, sort_keys=False
    )
    return EXAMPLES_HEADER + body


def write_examples(path: str | Path, examples: list[dict[str, Any]]) -> None:
    """examples.yaml を原子的に書き換える（一時ファイルに書いてから置き換える）。"""
    path = Path(path)
    text = dump_examples(examples)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def load_examples_document(path: str | Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("examples"), list):
        raise ExampleValidationError("examples.yaml に examples の一覧がありません。")
    return raw["examples"]


# --- 例題のグラフ ------------------------------------------------------------


def example_student_iri(example_id: str) -> str:
    return f"urdi:example/{example_id}/student"


def build_example_graph(example: dict[str, Any], base_graph: KnowledgeGraph) -> KnowledgeGraph:
    """base のナレッジグラフに、例題の学生と、その修得・履修登録の事実を足したグラフ。

    - 学生ノード ``urdi:example/{id}/student``（schema:Person ＋ ccso:UndergraduateStudent）
    - history の各要素 → schema:AchieveAction（``urdi:example/{id}/achievement/{academic_year}``）
    - other_credits → 仮想科目 ``urdi:example/{id}/course/{academic_year}``（0 または省略なら作らない）
    - registration → ccso:hasRegistered（仮想科目 ``urdi:example/{id}/course/registered`` を含む）と
      schema:RegisterAction（2026年度前期。completed に応じて Completed/Potential）

    ccso:hasCompleted は、history の全科目と仮想科目の和集合（KnowledgeGraph の検証と一致させる）。
    """
    example_id = str(example["id"])
    profile = example["student"]
    student_iri = example_student_iri(example_id)
    program = base_graph.program()
    program_iri = str(program["@id"])
    provider = base_graph.ref(program, "schema:provider")

    nodes: list[dict[str, Any]] = []
    student: dict[str, Any] = {
        "@id": student_iri,
        "@type": ["schema:Person", "ccso:UndergraduateStudent"],
        "schema:identifier": example_id,
        "schema:name": "例題の学生",
    }
    if provider:
        student["schema:affiliation"] = {"@id": provider}
    student["ccso:enrolledIn"] = {"@id": program_iri}
    student["urd:yearOfStudy"] = int(profile["year_of_study"])

    def virtual_course(suffix: str, credits: int) -> dict[str, Any]:
        return {
            "@id": f"urdi:example/{example_id}/course/{suffix}",
            "@type": ["schema:Course", "ccso:Course"],
            "schema:name": f"その他の科目（{credits}単位分）",
            "schema:courseCode": f"{example_id}-{suffix}",
            "schema:numberOfCredits": credits,
        }

    history = list(profile.get("history") or [])
    completed: list[dict[str, str]] = []
    achievements: list[dict[str, Any]] = []
    for entry in history:
        academic_year = str(entry["academic_year"])
        year_of_study = int(entry["year_of_study"])
        objects = [{"@id": base_graph.course(str(code)).iri} for code in entry.get("courses") or []]
        other = int(entry.get("other_credits") or 0)
        if other:
            course = virtual_course(academic_year, other)
            nodes.append(course)
            objects.append({"@id": course["@id"]})
        completed.extend(objects)
        achievements.append(
            {
                "@id": f"urdi:example/{example_id}/achievement/{academic_year}",
                "@type": "schema:AchieveAction",
                "schema:name": f"{academic_year}年度（{year_of_study}年次）の単位修得",
                "schema:agent": {"@id": student_iri},
                "schema:object": objects,
                "schema:actionStatus": {"@id": "schema:CompletedActionStatus"},
                "ccso:academicYear": academic_year,
                "urd:atYearOfStudy": year_of_study,
            }
        )
    if history:
        student["ccso:hasCompleted"] = completed

    registration = profile.get("registration")
    register_actions: list[dict[str, Any]] = []
    if registration is not None:
        registered = [
            {"@id": base_graph.course(str(code)).iri} for code in registration.get("courses") or []
        ]
        other = int(registration.get("other_credits") or 0)
        if other:
            course = virtual_course("registered", other)
            nodes.append(course)
            registered.append({"@id": course["@id"]})
        student["ccso:hasRegistered"] = registered
        status = (
            "schema:CompletedActionStatus"
            if registration.get("completed")
            else "schema:PotentialActionStatus"
        )
        register_actions.append(
            {
                "@id": f"urdi:example/{example_id}/registration-{REGISTRATION_ACADEMIC_YEAR}-{REGISTRATION_SEMESTER}",
                "@type": "schema:RegisterAction",
                "schema:name": f"{REGISTRATION_ACADEMIC_YEAR}年度前期 履修登録",
                "schema:agent": {"@id": student_iri},
                "schema:object": {"@id": program_iri},
                "schema:actionStatus": {"@id": status},
                "ccso:academicYear": REGISTRATION_ACADEMIC_YEAR,
                "urd:semester": REGISTRATION_SEMESTER,
            }
        )

    document = {
        "@context": dict(base_graph.context),
        "@graph": [*base_graph.nodes.values(), student, *register_actions, *achievements, *nodes],
    }
    return KnowledgeGraph.from_document(document)


def describe_student(example: dict[str, Any], base_graph: KnowledgeGraph) -> str:
    """例題の学生を業務語で説明する（CURIE や科目コードは出さない）。

    例「現在2年生。2025年度（1年次）に31単位を修得（必修Aを含む）。合計31単位。」
    """
    profile = example["student"]
    parts = [f"現在{int(profile['year_of_study'])}年生。"]
    history = list(profile.get("history") or [])
    if not history:
        parts.append("本学での修得の記録はない。")
    else:
        total = 0
        for entry in history:
            courses = [base_graph.course(str(code)) for code in entry.get("courses") or []]
            credits = sum(course.credits for course in courses) + int(entry.get("other_credits") or 0)
            total += credits
            required = [
                course.name
                for course in courses
                if base_graph.ref(base_graph.node(course.iri), "urd:courseCategory") == REQUIRED_CATEGORY
            ]
            note = f"（{'・'.join(required)}を含む）" if required else ""
            parts.append(
                f"{entry['academic_year']}年度（{entry['year_of_study']}年次）に{credits}単位を修得{note}。"
            )
        parts.append(f"合計{total}単位。")
    registration = profile.get("registration")
    if registration is not None:
        courses = [base_graph.course(str(code)) for code in registration.get("courses") or []]
        credits = sum(course.credits for course in courses) + int(registration.get("other_credits") or 0)
        state = "登録済み" if registration.get("completed") else "登録は未完了"
        parts.append(f"{REGISTRATION_ACADEMIC_YEAR}年度前期に{credits}単位を履修登録（{state}）。")
    return "".join(parts)


# --- 評価 ---------------------------------------------------------------------


class ExampleOutcome(BaseModel):
    """例題に対するシステムの判定。proof は RuleEngine の証明木（読み下しつき）そのもの。"""

    status: str
    message: str
    unmet: list[str] = Field(default_factory=list)
    proof: dict[str, Any] | None = None


def evaluate_example(
    example: dict[str, Any], rules_path: str | Path, base_graph: KnowledgeGraph
) -> ExampleOutcome:
    """例題の学生を足したグラフの上で、rule_type に対応する RuleEngine の判定を実行する。"""
    _, method, _ = EXAMPLE_RULE_TYPES[str(example["rule_type"])]
    graph = build_example_graph(example, base_graph)
    engine = RuleEngine(rules_path, graph)
    iri = example_student_iri(str(example["id"]))
    student = next(item for item in graph.students() if item.iri == iri)
    try:
        result = getattr(engine, method)(student)
    except ValueError as exc:
        return ExampleOutcome(
            status=DecisionStatus.UNKNOWN.value,
            message=f"このプロトタイプに登録されているルールでは判定できません（{exc}）。",
        )
    return ExampleOutcome(
        status=result.status.value,
        message=result.message,
        unmet=list(result.unmet_conditions),
        proof=result.proof,
    )


def effective_expected(example: dict[str, Any]) -> str:
    """照合に使う期待結論。担当者が「正しくない」とした例題は、担当者の正解を使う。"""
    review = example.get("review") or {}
    if review.get("verdict") == "disagree":
        return str(review.get("correct_status"))
    return str(example["expected"]["status"])


def matches(example: dict[str, Any], outcome: ExampleOutcome) -> bool:
    return outcome.status == effective_expected(example)


def is_reviewed(example: dict[str, Any]) -> bool:
    return (example.get("review") or {}).get("verdict", "unreviewed") != "unreviewed"


# --- リポジトリ ---------------------------------------------------------------


class ExampleRepository:
    """examples.yaml を検証し、担当者の確認結果（review）を原子的に書き戻す。"""

    def __init__(self, path: str | Path, base_graph: KnowledgeGraph):
        self.path = Path(path)
        self.graph = base_graph

    def load(self) -> list[dict[str, Any]]:
        examples = load_examples_document(self.path)
        self.validate(examples)
        return examples

    def validate(self, examples: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        for index, example in enumerate(examples):
            if not isinstance(example, dict):
                raise ExampleValidationError(f"{index + 1}件目の例題が辞書ではありません。")
            example_id = example.get("id")
            if not isinstance(example_id, str) or not example_id:
                raise ExampleValidationError(f"{index + 1}件目の例題に id がありません。")
            if example_id in seen:
                raise ExampleValidationError(f"例題の id が重複しています: {example_id}")
            seen.add(example_id)
            self._validate_example(example_id, example)

    def _validate_example(self, example_id: str, example: dict[str, Any]) -> None:
        def fail(message: str) -> None:
            raise ExampleValidationError(f"{example_id}: {message}")

        if example.get("rule_type") not in EXAMPLE_RULE_TYPES:
            fail(f"rule_type が不明です: {example.get('rule_type')}")
        if example.get("origin") not in ORIGINS:
            fail(f"origin が不明です: {example.get('origin')}")
        if not example.get("title"):
            fail("title がありません。")

        profile = example.get("student")
        if not isinstance(profile, dict):
            fail("student がありません。")
        if not self._positive_int(profile.get("year_of_study")):
            fail("student.year_of_study は1以上の整数です。")
        history = profile.get("history") or []
        if not isinstance(history, list):
            fail("student.history は一覧です。")
        completed: set[str] = set()
        academic_years: set[str] = set()
        for entry in history:
            if not isinstance(entry, dict):
                fail("student.history の要素が辞書ではありません。")
            if not self._positive_int(entry.get("academic_year")):
                fail("history の academic_year は整数です。")
            if str(entry["academic_year"]) in academic_years:
                fail(f"同じ年度の記録が2つあります: {entry['academic_year']}")
            academic_years.add(str(entry["academic_year"]))
            if not self._positive_int(entry.get("year_of_study")):
                fail("history の year_of_study は1以上の整数です。")
            for code in self._courses(fail, entry):
                if code in completed:
                    fail(f"同じ科目が2つの年度で修得されています: {code}")
                completed.add(code)
            self._other_credits(fail, entry)
        registration = profile.get("registration")
        if registration is not None:
            if not isinstance(registration, dict):
                fail("student.registration が辞書ではありません。")
            self._courses(fail, registration)
            self._other_credits(fail, registration)
            if not isinstance(registration.get("completed", False), bool):
                fail("registration.completed は true / false です。")

        expected = example.get("expected")
        if not isinstance(expected, dict) or expected.get("status") not in EXPECTED_STATUSES:
            fail(f"expected.status は {' / '.join(EXPECTED_STATUSES)} のいずれかです。")

        review = example.get("review")
        if not isinstance(review, dict):
            fail("review がありません。")
        verdict = review.get("verdict")
        if verdict not in VERDICTS:
            fail(f"review.verdict が不明です: {verdict}")
        correct = review.get("correct_status")
        if verdict == "disagree":
            if correct not in EXPECTED_STATUSES:
                fail("「正しくない」とした例題には、正しい結論（correct_status）が必要です。")
        elif correct is not None:
            fail("correct_status は verdict が disagree のときだけ書きます。")

    @staticmethod
    def _positive_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 1

    def _courses(self, fail: Any, entry: dict[str, Any]) -> list[str]:
        courses = entry.get("courses") or []
        if not isinstance(courses, list):
            fail("courses は科目コードの一覧です。")
        for code in courses:
            if not self.graph.has_course(str(code)):
                fail(f"科目コードがナレッジグラフにありません: {code}")
        return [str(code) for code in courses]

    @staticmethod
    def _other_credits(fail: Any, entry: dict[str, Any]) -> None:
        other = entry.get("other_credits", 0)
        if other is None:
            return
        if not isinstance(other, int) or isinstance(other, bool) or other < 0:
            fail("other_credits は0以上の整数です。")

    def save_reviews(self, reviews: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """例題 id → review の対応で確認結果を書き戻す。検証に通らなければ何も書かない。"""
        examples = load_examples_document(self.path)
        by_id = {example.get("id"): example for example in examples}
        for example_id, review in reviews.items():
            if example_id not in by_id:
                raise ExampleValidationError(f"例題がありません: {example_id}")
            by_id[example_id]["review"] = {**empty_review(), **review}
        self.validate(examples)
        write_examples(self.path, examples)
        return examples

