"""オントロジーの語彙定義。実体は data/ontology_graph.yaml にある。

このモジュールは「どの概念を、どの共通語彙のどの用語で表すか」を Rule Engine へ渡す。
定義そのものはグラフ文書（node / edge / hypernode）が持ち、ここはそれを
Python から扱いやすい形へ射影するだけである。docs/graph.html で編集した結果を
グラフ文書へ書き戻せば、判定・検証・画面表示のすべてに反映される。

設計方針（グラフ文書の rationale hypernode にも同じ内容が入っている）:

1. schema.org に適切な用語がある概念は、必ず schema.org を使う。
2. schema.org に無く CCSO にある概念（在籍・修得・履修登録など学籍側）は CCSO を使う。
3. どちらにも無い概念だけ、ローカル拡張名前空間 ``urd:`` を定義し、
   近い既存用語があれば ``skos:closeMatch`` として明示する（同一視はしない）。

用語の実在は tests/test_ontology.py が data/schemaorg_terms.json と
data/ccso_terms.json（各語彙の公式配布物から抽出した部分集合）に対して検証する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.graph_document import GraphDocument

GRAPH_PATH = Path(__file__).resolve().parents[1] / "data" / "ontology_graph.yaml"

#: 語彙定義の実体。読み取り専用として扱う。
GRAPH = GraphDocument(GRAPH_PATH)


def _namespace(prefix: str) -> str:
    for namespace in GRAPH.namespaces:
        if namespace.prefix == prefix:
            return namespace.iri
    raise KeyError(f"接頭辞が未登録です: {prefix}")


def _version(prefix: str) -> str:
    for namespace in GRAPH.namespaces:
        if namespace.prefix == prefix:
            return namespace.version or ""
    return ""


# --- 名前空間 -------------------------------------------------------------

SCHEMA = _namespace("schema")
CCSO = _namespace("ccso")
OLOUD = _namespace("oloud")
SKOS = _namespace("skos")
RDFS = _namespace("rdfs")
XSD = _namespace("xsd")

#: 本デモのローカル拡張「語彙」の名前空間。GitHub Pages 版が用語の説明を返す。
URD = _namespace("urd")

#: 本デモの「インスタンス」（実在の学生・科目・規程ノード）の名前空間。
#: 語彙（urd:）とデータ（urdi:）を分けることで、用語かノードかを接頭辞だけで判別できる。
URD_ID = _namespace("urdi")

#: 語彙の版。schema.org 30.0（2026-03-19 リリース）で用語の実在を確認している。
SCHEMA_ORG_VERSION = _version("schema")

#: CCSO の版（ccso.owl の owl:versionInfo）。
CCSO_VERSION = _version("ccso")

JSONLD_CONTEXT: dict[str, str] = GRAPH.context()


def expand(curie: str) -> str:
    """``schema:name`` のようなCURIEを絶対IRIへ展開する。"""
    prefix, _, local = curie.partition(":")
    namespace = JSONLD_CONTEXT.get(prefix)
    if not local or namespace is None:
        raise ValueError(f"未知の名前空間です: {curie}")
    return f"{namespace}{local}"


# --- 学生（学籍データ）側の用語 -------------------------------------------
#
# schema.org は科目カタログ・規程・通知の語彙は十分に持つが、
# 「誰が何を修得したか」という学籍側の語彙をほとんど持たない。
# schema:numberOfCredits の domain も Course と EducationalOccupationalProgram
# だけで、Person には付けられない。そこを CCSO が埋める。
#
# 下の2つは Student モデルの属性名と用語を結ぶ「コード側の束縛」なので、
# グラフ文書ではなくここに置く。用語が実在することは
# tests/test_graph_document.py がグラフ文書に対して検証する。

STUDENT_TERMS: dict[str, str] = {
    "student_id": "schema:identifier",
    "name": "schema:name",
    "faculty": "schema:affiliation",
    "year_of_study": "urd:yearOfStudy",
    "earned_credits": "urd:earnedCredits",
    "current_registered_credits": "urd:registeredCredits",
    "completed_courses": "ccso:hasCompleted",
    "registered_courses": "ccso:hasRegistered",
    "registration_completed": "schema:actionStatus",
    "enrolled_program": "ccso:enrolledIn",
}

#: 個別通知ルールの条件で指定できる用語 → (Studentの属性名, 型)
CONDITION_TERMS: dict[str, tuple[str, type]] = {
    "urd:yearOfStudy": ("year_of_study", int),
    "urd:earnedCredits": ("earned_credits", int),
    "urd:registeredCredits": ("current_registered_credits", int),
    "schema:actionStatus": ("registration_completed", bool),
    "ccso:hasCompleted": ("completed_courses", list),
    "ccso:hasRegistered": ("registered_courses", list),
}

CONDITION_TERM_LABELS: dict[str, str] = {
    term: GRAPH.term(term).label for term in CONDITION_TERMS
}


# --- ルール種別ごとのオントロジー束縛 ---------------------------------------


class RuleOntology:
    """ルール種別ひとつ分の語彙束縛。

    Attributes:
        label: 画面表示名。
        subject_class: そのルールが制約する主語のクラス（CURIE）。
        rule_terms: ルール側の設定値が対応する用語（YAMLキー → CURIE）。
        evaluated_terms: 判定時に学生・グラフから読む用語（CURIE）。
        horn_clause: 判定を Horn節 として書き下したもの。処理系には渡さず、
            「どの原子論理式を根拠にしたか」を人間とレビュアーへ示すために使う。
    """

    def __init__(
        self,
        label: str,
        subject_class: str,
        rule_terms: dict[str, str],
        evaluated_terms: list[str],
        horn_clause: str,
    ):
        self.label = label
        self.subject_class = subject_class
        self.rule_terms = rule_terms
        self.evaluated_terms = evaluated_terms
        self.horn_clause = horn_clause

    def terms(self) -> list[str]:
        """このルールが触れる全用語（重複排除・出現順）。"""
        ordered = [self.subject_class, *self.rule_terms.values(), *self.evaluated_terms]
        seen: dict[str, None] = {}
        for term in ordered:
            seen.setdefault(term, None)
        return list(seen)


def _load_rule_types() -> dict[str, RuleOntology]:
    rule_types: dict[str, RuleOntology] = {}
    for node in GRAPH.nodes_of_kind("rule-type"):
        rule_type = str(getattr(node, "rule_type"))
        horn_clause = " ".join(str(getattr(node, "horn_clause")).split())
        rule_types[rule_type] = RuleOntology(
            label=node.label,
            subject_class=str(getattr(node, "subject_class")),
            rule_terms=dict(getattr(node, "rule_terms", None) or {}),
            evaluated_terms=list(getattr(node, "evaluated_terms", None) or []),
            horn_clause=horn_clause,
        )
    return rule_types


RULE_TYPE_ONTOLOGY: dict[str, RuleOntology] = _load_rule_types()

RULE_TYPE_LABELS: dict[str, str] = {
    rule_type: ontology.label for rule_type, ontology in RULE_TYPE_ONTOLOGY.items()
}


def _singleton_rule_types() -> set[str]:
    """1種別1件しか登録できないルール種別。

    グラフ文書の hypernode ``rule-group/institutional`` のメンバーが定義そのもの。
    """
    members = GRAPH.get("rule-group/institutional").members  # type: ignore[union-attr]
    return {str(getattr(GRAPH.get(member), "rule_type")) for member in members}


SINGLETON_RULE_TYPES = _singleton_rule_types()


# --- 語彙対応表 -------------------------------------------------------------
#
# 表は文書に書かない。concept → term の uses / alternative エッジから導出する。
# CCSO は自身が schema:Organization / schema:EducationalOrganization を
# 直接再利用しており（ccso.owl で確認）、schema.org と併用できる。
# OLOUD は AIISO / FOAF / Dublin Core の上に構築されており schema.org は使わない。

CROSSWALK: list[dict[str, str]] = GRAPH.crosswalk()

#: ローカル拡張用語の定義。JSON-LD へ rdfs:label / skos:closeMatch として出力する。
LOCAL_TERM_DEFINITIONS: dict[str, dict[str, Any]] = {
    str(getattr(node, "curie")): {
        "label": node.label,
        "comment": str(getattr(node, "comment", "") or ""),
        "close_match": list(getattr(node, "close_match", None) or []),
    }
    for node in GRAPH.terms()
    if str(getattr(node, "curie")).startswith("urd:")
}


def all_curies() -> list[str]:
    """デモが使用する全CURIE（テストで実在検証する対象）。"""
    curies: dict[str, None] = {}
    for term in STUDENT_TERMS.values():
        curies.setdefault(term, None)
    for term in CONDITION_TERMS:
        curies.setdefault(term, None)
    for ontology in RULE_TYPE_ONTOLOGY.values():
        for term in ontology.terms():
            curies.setdefault(term, None)
    for term in LOCAL_TERM_DEFINITIONS:
        curies.setdefault(term, None)
    return list(curies)
