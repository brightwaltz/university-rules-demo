"""グラフ文書（node / edge / hypernode）の検証。

data/ontology_graph.yaml は表示用の複製ではなく、src/ontology.py が読み込む
語彙定義そのものである。壊れた編集がそのまま通らないことを確かめる。
"""

import copy
from pathlib import Path

import pytest
import yaml

from src.graph_document import GraphDocument, GraphDocumentError
from src.ontology import (
    CONDITION_TERMS,
    GRAPH,
    LOCAL_TERM_DEFINITIONS,
    RULE_TYPE_ONTOLOGY,
    SINGLETON_RULE_TYPES,
    STUDENT_TERMS,
)


ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "data" / "ontology_graph.yaml"


def write_variant(tmp_path: Path, mutate) -> Path:
    document = yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "variant.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


# --- 構造 -------------------------------------------------------------------


def test_graph_document_loads_and_validates():
    document = GraphDocument(GRAPH_PATH)
    assert document.meta["title"]
    assert len(document.nodes) > 60
    assert len(document.hypernodes) >= 10
    assert document.edges


def test_hypernodes_can_nest():
    """階層構造：hypernode が hypernode を含められること。"""
    children = GRAPH.get("layer/vocabulary").members
    assert "layer/schema-org" in children
    descendants = GRAPH.descendants("layer/vocabulary")
    assert "term/ccso:hasCompleted" in descendants
    assert "layer/ccso" in descendants
    assert "layer/vocabulary" in GRAPH.parents_of("layer/schema-org")


def test_rationale_hypernodes_carry_meta_knowledge():
    """メタ知識：集合についての言明を持つ hypernode。"""
    rationale = {h.id: h for h in GRAPH.hypernodes_of_kind("rationale")}
    assert rationale, "rationale hypernode がありません"
    why_ccso = rationale["rationale/why-ccso"]
    assert "Person には付けられない" in why_ccso.statement
    assert why_ccso.evidence
    # about は node にも hypernode にも張れる
    assert "domain/academic-record" in why_ccso.about
    assert "term/schema:numberOfCredits" in why_ccso.about
    for hypernode in rationale.values():
        assert hypernode.statement, hypernode.id


def test_hypernode_can_be_an_edge_endpoint():
    endpoints = {edge.target for edge in GRAPH.edges}
    assert "layer/schema-org" in endpoints


def test_derived_edges_are_not_written_in_the_document():
    """導出できる関係は文書に書かない（二重管理を避ける）。"""
    raw = yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8"))
    written = {(edge["source"], edge["target"], edge["kind"]) for edge in raw["edges"]}
    for edge in GRAPH.derived_edges:
        assert (edge.source, edge.target, edge.kind) not in written, edge.id
    assert len(GRAPH.derived_edges) > 20


def test_every_edge_kind_is_defined_with_meaning():
    """関係の種類は、名前・説明・読み下し文を必ず持つ。"""
    assert len(GRAPH.edge_kinds) >= 10
    for kind in GRAPH.edge_kinds:
        assert kind.label, kind.id
        assert kind.description, kind.id
        assert "{source}" in kind.reading and "{target}" in kind.reading, kind.id


def test_every_edge_kind_used_is_declared():
    declared = {kind.id for kind in GRAPH.edge_kinds}
    used = {edge.kind for edge in GRAPH.edges}
    assert used <= declared, f"未定義の種類: {sorted(used - declared)}"


def test_every_edge_reads_as_a_sentence():
    """どのエッジも、意味が日本語の一文になること。"""
    for edge in GRAPH.edges:
        sentence = GRAPH.describe_edge(edge)
        assert sentence and "{" not in sentence, edge.id
        # 端点の呼び名が両方入っていること（用語は CURIE で示す）
        assert GRAPH.endpoint_label(edge.source) in sentence, edge.id
        assert GRAPH.endpoint_label(edge.target) in sentence, edge.id


def test_reading_uses_curie_for_terms_to_avoid_ambiguity():
    edge = next(e for e in GRAPH.edges
                if e.source == "concept/course" and e.kind == "uses")
    assert GRAPH.describe_edge(edge) == "科目 は schema:Course で表す"


def test_edge_specific_label_is_appended():
    edge = next(e for e in GRAPH.edges if e.label == "修得科目をたどって")
    assert GRAPH.describe_edge(edge) == (
        "urd:earnedCredits は ccso:hasCompleted をたどって導出する（修得科目をたどって）"
    )


def test_exported_graph_carries_the_reading_of_every_edge():
    payload = GRAPH.to_dict()
    assert len(payload["edge_kinds"]) == len(GRAPH.edge_kinds)
    for row in payload["edges"]:
        assert row["reading"]


def test_undeclared_edge_kind_is_rejected(tmp_path):
    def mutate(document):
        document["edges"][0]["kind"] = "made-up"

    with pytest.raises(GraphDocumentError, match="edge_kinds に定義の無い種類"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_derived_edges_cover_every_rule_type_binding():
    for rule_type, ontology in RULE_TYPE_ONTOLOGY.items():
        node_id = f"rule-type/{rule_type}"
        kinds = {edge.kind for edge in GRAPH.edges if edge.source == node_id}
        assert "constrains" in kinds, rule_type
        if ontology.rule_terms:
            assert "sets" in kinds, rule_type


# --- Python 側との接続 -------------------------------------------------------


def test_ontology_module_reads_the_graph_document():
    assert RULE_TYPE_ONTOLOGY["thesis_eligibility"].subject_class == "schema:Course"
    assert "eligible_to_register" in RULE_TYPE_ONTOLOGY["thesis_eligibility"].horn_clause
    assert LOCAL_TERM_DEFINITIONS["urd:requiredCourse"]["close_match"] == [
        "schema:programPrerequisites",
        "ccso:hasPrerequisite",
    ]


def test_singleton_rule_types_come_from_a_hypernode():
    """1種別1件という制約を、hypernode のメンバーとして持っている。"""
    assert SINGLETON_RULE_TYPES == {
        "registration_deadline",
        "annual_credit_limit",
        "graduation_credit_requirement",
        "graduation_required_courses",
        "thesis_eligibility",
    }
    assert "personalized_notification" not in SINGLETON_RULE_TYPES


def test_code_level_term_bindings_exist_in_the_graph():
    """Python 側に置いた束縛の用語が、グラフ文書に実在すること。"""
    for curie in {*STUDENT_TERMS.values(), *CONDITION_TERMS}:
        assert GRAPH.has_term(curie), curie


def test_crosswalk_is_derived_from_edges():
    rows = {row["concept_id"]: row for row in GRAPH.crosswalk()}
    assert rows["concept/completed-course"]["demo"] == "ccso:hasCompleted"
    assert rows["concept/completed-course"]["schema_org"] == "（該当なし）"
    assert rows["concept/course"]["schema_org"] == "schema:Course"
    assert rows["concept/course"]["ccso"] == "ccso:Course"
    assert rows["concept/student"]["demo"] == "schema:Person ＋ ccso:UndergraduateStudent"


# --- 壊れた編集を弾く -------------------------------------------------------


def test_missing_edge_endpoint_is_rejected(tmp_path):
    def mutate(document):
        document["edges"].append(
            {"source": "concept/course", "target": "term/does:not-exist", "kind": "uses"}
        )

    with pytest.raises(GraphDocumentError, match="端点が存在しません"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_missing_member_is_rejected(tmp_path):
    def mutate(document):
        document["hypernodes"][0]["members"].append("term/nope")

    with pytest.raises(GraphDocumentError, match="メンバーが存在しません"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_membership_cycle_is_rejected(tmp_path):
    def mutate(document):
        by_id = {item["id"]: item for item in document["hypernodes"]}
        by_id["layer/schema-org"].setdefault("members", []).append("layer/vocabulary")

    with pytest.raises(GraphDocumentError, match="循環"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_unknown_kind_is_rejected(tmp_path):
    def mutate(document):
        document["nodes"][0]["kind"] = "something-else"

    with pytest.raises(GraphDocumentError, match="未知の node kind"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_duplicate_id_is_rejected(tmp_path):
    def mutate(document):
        document["nodes"].append(copy.deepcopy(document["nodes"][0]))

    with pytest.raises(GraphDocumentError, match="重複"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_unregistered_prefix_is_rejected(tmp_path):
    def mutate(document):
        for node in document["nodes"]:
            if node.get("curie") == "schema:Course":
                node["curie"] = "unknown:Course"

    with pytest.raises(GraphDocumentError, match="未登録の接頭辞"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_rationale_without_statement_is_rejected(tmp_path):
    def mutate(document):
        for hypernode in document["hypernodes"]:
            if hypernode["kind"] == "rationale":
                hypernode.pop("statement", None)
                break

    with pytest.raises(GraphDocumentError, match="statement が必要"):
        GraphDocument(write_variant(tmp_path, mutate))


def test_close_match_to_unknown_term_is_rejected(tmp_path):
    def mutate(document):
        for node in document["nodes"]:
            if node.get("curie") == "urd:requiredCourse":
                node["close_match"] = ["schema:doesNotExistHere"]

    with pytest.raises(GraphDocumentError, match="用語ノードがありません"):
        GraphDocument(write_variant(tmp_path, mutate))


# --- 書き戻し ---------------------------------------------------------------


def test_to_yaml_round_trips(tmp_path):
    path = tmp_path / "roundtrip.yaml"
    path.write_text(GRAPH.to_yaml(), encoding="utf-8")
    again = GraphDocument(path)
    assert [node.id for node in again.nodes] == [node.id for node in GRAPH.nodes]
    assert [h.id for h in again.hypernodes] == [h.id for h in GRAPH.hypernodes]
    assert [e.id for e in again.explicit_edges] == [e.id for e in GRAPH.explicit_edges]
    assert again.crosswalk() == GRAPH.crosswalk()
