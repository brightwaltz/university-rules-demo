"""JSON-LD ナレッジグラフの読み込みと、内部モデルへの射影。

外部ライブラリを使わない小さな読み取り専用ローダー。扱うのは
``data/university_graph.jsonld`` が使う JSON-LD の部分集合だけである。

- ``@context`` は接頭辞→IRI の対応のみ（リモート文脈は読まない）
- ノードのキーはすべてCURIE
- 値は リテラル / ``{"@id": ...}`` / ``{"@value": ..., "@type": ...}`` / それらの配列

意図的に部分実装にしている。完全なJSON-LD処理は rdflib が行い、
``tests/test_jsonld_interop.py`` が同じファイルを rdflib で読み込んで
SPARQL で問い合わせられることを検証する。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.models import CourseInfo, LegislationRef, Student
from src.ontology import JSONLD_CONTEXT


class KnowledgeGraphError(ValueError):
    """グラフの構造がデモの前提を満たさないことを示す。"""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _literal(value: Any) -> Any:
    """``{"@value": ...}`` 形式を素の値へ落とす。"""
    if isinstance(value, dict) and "@value" in value:
        return value["@value"]
    return value


class KnowledgeGraph:
    """``@id`` で索引した読み取り専用のグラフ。"""

    def __init__(self, path: str | Path):
        path = Path(path)
        document = json.loads(path.read_text(encoding="utf-8"))
        self._load(document, path)

    @classmethod
    def from_document(cls, document: dict[str, Any], path: str | Path | None = None) -> "KnowledgeGraph":
        """読み込み済みの JSON-LD 文書（辞書）からグラフを作る。

        例題のように、ファイルに書かずに組み立てたグラフを判定に掛けるときに使う。
        検証はファイルから読んだときと同じものを通す。
        """
        graph = cls.__new__(cls)
        graph._load(document, Path(path) if path is not None else None)
        return graph

    def _load(self, document: Any, path: Path | None) -> None:
        self.path = path
        if not isinstance(document, dict) or "@graph" not in document:
            raise KnowledgeGraphError("JSON-LDに @graph がありません。")
        self.context: dict[str, str] = dict(document.get("@context", {}))
        self.nodes: dict[str, dict[str, Any]] = {}
        for node in document["@graph"]:
            node_id = node.get("@id")
            if not node_id:
                raise KnowledgeGraphError("@id を持たないノードがあります。")
            if node_id in self.nodes:
                raise KnowledgeGraphError(f"@id が重複しています: {node_id}")
            self.nodes[node_id] = node

        missing = [prefix for prefix in JSONLD_CONTEXT if prefix not in self.context]
        if missing:
            raise KnowledgeGraphError(f"@context に必要な接頭辞がありません: {missing}")

        self._courses_by_code: dict[str, CourseInfo] = {}
        for node in self.nodes_of_type("schema:Course"):
            course = self._to_course(node)
            if course.code in self._courses_by_code:
                raise KnowledgeGraphError(f"科目コードが重複しています: {course.code}")
            self._courses_by_code[course.code] = course

        self._check_completion_records()

    # --- 低レベルアクセス -------------------------------------------------

    def node(self, iri: str) -> dict[str, Any]:
        try:
            return self.nodes[iri]
        except KeyError as exc:
            raise KnowledgeGraphError(f"ノードが見つかりません: {iri}") from exc

    def has_node(self, iri: str) -> bool:
        return iri in self.nodes

    def types_of(self, node: dict[str, Any]) -> list[str]:
        return [str(item) for item in _as_list(node.get("@type"))]

    def nodes_of_type(self, curie: str) -> list[dict[str, Any]]:
        return [node for node in self.nodes.values() if curie in self.types_of(node)]

    def value(self, node: dict[str, Any], term: str, default: Any = None) -> Any:
        """述語 ``term`` のリテラル値をひとつ返す。"""
        values = _as_list(node.get(term))
        if not values:
            return default
        return _literal(values[0])

    def refs(self, node: dict[str, Any], term: str) -> list[str]:
        """述語 ``term`` が指すノードの ``@id`` 一覧を返す。"""
        result: list[str] = []
        for item in _as_list(node.get(term)):
            if isinstance(item, dict) and "@id" in item:
                result.append(str(item["@id"]))
        return result

    def ref(self, node: dict[str, Any], term: str) -> str | None:
        refs = self.refs(node, term)
        return refs[0] if refs else None

    def label_of(self, iri: str) -> str:
        """ノードの schema:name。無ければ ``@id`` をそのまま返す。"""
        if not self.has_node(iri):
            return iri
        return str(self.value(self.node(iri), "schema:name", iri))

    # --- 領域ごとの射影 ---------------------------------------------------

    def _to_course(self, node: dict[str, Any]) -> CourseInfo:
        category_iri = self.ref(node, "urd:courseCategory")
        return CourseInfo(
            iri=str(node["@id"]),
            code=str(self.value(node, "schema:courseCode", "")),
            name=str(self.value(node, "schema:name", "")),
            credits=int(self.value(node, "schema:numberOfCredits", 0)),
            category=self.label_of(category_iri) if category_iri else None,
        )

    def courses(self) -> list[CourseInfo]:
        return list(self._courses_by_code.values())

    def course(self, code: str) -> CourseInfo:
        try:
            return self._courses_by_code[code]
        except KeyError as exc:
            raise KnowledgeGraphError(f"科目コードがグラフにありません: {code}") from exc

    def has_course(self, code: str) -> bool:
        return code in self._courses_by_code

    def course_label(self, code: str) -> str:
        """科目コードの表示名。未登録コードはコードのまま返す（判定は止めない）。"""
        course = self._courses_by_code.get(code)
        return course.name if course else code

    def program(self) -> dict[str, Any]:
        programs = self.nodes_of_type("schema:EducationalOccupationalProgram")
        if len(programs) != 1:
            raise KnowledgeGraphError(
                f"schema:EducationalOccupationalProgram は1件である必要があります（{len(programs)}件）。"
            )
        return programs[0]

    def legislation(self, iri: str | None) -> LegislationRef | None:
        """条文ノードを参照へ射影する。未登録・未指定なら None。"""
        if not iri or not self.has_node(iri):
            return None
        node = self.node(iri)
        if "schema:Legislation" not in self.types_of(node):
            return None
        parent_iri = self.ref(node, "schema:isPartOf")
        parent_name = self.label_of(parent_iri) if parent_iri else None
        return LegislationRef(
            iri=iri,
            identifier=str(self.value(node, "schema:legislationIdentifier", "")),
            name=str(self.value(node, "schema:name", "")),
            parent_iri=parent_iri,
            parent_name=parent_name,
            text=self.value(node, "schema:text"),
        )

    def legislation_articles(self) -> list[LegislationRef]:
        """条文（親を持つ schema:Legislation）の一覧。"""
        articles = []
        for node in self.nodes_of_type("schema:Legislation"):
            if self.ref(node, "schema:isPartOf") is None:
                continue
            reference = self.legislation(str(node["@id"]))
            if reference is not None:
                articles.append(reference)
        return articles

    def _credits_for(self, node: dict[str, Any], term: str) -> tuple[int, list[str]]:
        """``term`` が指す科目の単位数を合計し、科目コードも返す。"""
        total = 0
        codes: list[str] = []
        for course_iri in self.refs(node, term):
            course = self._to_course(self.node(course_iri))
            total += course.credits
            codes.append(course.code)
        return total, codes

    # --- 修得の時期（schema:AchieveAction） --------------------------------
    #
    # ccso:hasCompleted は「いつ修得したか」を持たない。履修登録を schema:RegisterAction で
    # 持っているのと同じ流儀で、学生×年度ごとに1つの schema:AchieveAction を置き、
    # schema:object に修得した科目、urd:atYearOfStudy に修得時の学年を持たせる。
    # ccso:hasCompleted は相互運用のための要約として残すので、両者の整合を読み込み時に確かめる。

    def _achievements_of(self, student_iri: str) -> list[dict[str, Any]]:
        return [
            action
            for action in self.nodes_of_type("schema:AchieveAction")
            if self.ref(action, "schema:agent") == student_iri
        ]

    def _check_completion_records(self) -> None:
        """修得の記録と ccso:hasCompleted が食い違っていないことを確かめる。

        修得の記録を1件でも持つ学生について、記録の schema:object の和集合が
        ccso:hasCompleted と一致し、同じ科目が2つの記録に現れないこと。
        """
        for student in self.nodes_of_type("schema:Person"):
            student_iri = str(student["@id"])
            actions = self._achievements_of(student_iri)
            if not actions:
                continue
            seen: dict[str, str] = {}
            for action in actions:
                action_iri = str(action["@id"])
                for course_iri in self.refs(action, "schema:object"):
                    if course_iri in seen:
                        raise KnowledgeGraphError(
                            f"同じ科目が複数の修得記録に現れます: {course_iri}"
                            f"（{seen[course_iri]} と {action_iri}）"
                        )
                    seen[course_iri] = action_iri
            completed = set(self.refs(student, "ccso:hasCompleted"))
            recorded = set(seen)
            if recorded != completed:
                missing = sorted(completed - recorded)
                extra = sorted(recorded - completed)
                raise KnowledgeGraphError(
                    f"修得の記録が ccso:hasCompleted と一致しません: {student_iri}"
                    f"（記録に無い科目: {missing}、ccso:hasCompleted に無い科目: {extra}）"
                )

    def completion_records(self, student_iri: str) -> list[dict[str, Any]]:
        """学生の年度ごとの修得記録を年度順に返す。

        各要素は academic_year（ccso:academicYear）, year_of_study（urd:atYearOfStudy）,
        course_codes（schema:object の科目コード）, credits（その単位数の合計）。
        """
        records: list[dict[str, Any]] = []
        for action in self._achievements_of(student_iri):
            credits, codes = self._credits_for(action, "schema:object")
            records.append(
                {
                    "academic_year": str(self.value(action, "ccso:academicYear", "")),
                    "year_of_study": int(self.value(action, "urd:atYearOfStudy", 0)),
                    "course_codes": codes,
                    "credits": credits,
                }
            )
        records.sort(key=lambda record: (record["academic_year"], record["year_of_study"]))
        return records

    def _registration_completed(self, student_iri: str) -> bool:
        """schema:RegisterAction の schema:actionStatus から履修登録の完了を読む。"""
        for action in self.nodes_of_type("schema:RegisterAction"):
            if self.ref(action, "schema:agent") != student_iri:
                continue
            status = self.ref(action, "schema:actionStatus")
            return status == "schema:CompletedActionStatus"
        return False

    def students(self) -> list[Student]:
        """schema:Person ノードを Student へ射影する。単位数はグラフから集計する。"""
        students: list[Student] = []
        for node in self.nodes_of_type("schema:Person"):
            iri = str(node["@id"])
            earned, completed = self._credits_for(node, "ccso:hasCompleted")
            registered, registered_codes = self._credits_for(node, "ccso:hasRegistered")
            affiliation_iri = self.ref(node, "schema:affiliation")
            students.append(
                Student(
                    student_id=str(self.value(node, "schema:identifier", "")),
                    name=str(self.value(node, "schema:name", "")),
                    faculty=self.label_of(affiliation_iri) if affiliation_iri else "",
                    year_of_study=int(self.value(node, "urd:yearOfStudy", 1)),
                    earned_credits=earned,
                    current_registered_credits=registered,
                    completed_courses=completed,
                    registered_courses=registered_codes,
                    registration_completed=self._registration_completed(iri),
                    iri=iri,
                )
            )
        students.sort(key=lambda student: student.student_id)
        return students
