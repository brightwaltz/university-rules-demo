"""判定結果・通知・ルールを JSON-LD として書き出す。

目的は「他のプログラムが同じ語彙でこのシステムの出力を読めること」。
出力は data/university_graph.jsonld と同じ ``@context`` を使うため、
グラフと判定結果を同じトリプルストアへ流し込める。
"""

from __future__ import annotations

from typing import Any

from src.knowledge_graph import KnowledgeGraph
from src.models import DecisionResult, Notification, Premise, Rule, Student
from src.ontology import JSONLD_CONTEXT, RULE_TYPE_ONTOLOGY

RULE_ID_PREFIX = "urdi:rule/"
NOTIFICATION_ID_PREFIX = "urdi:notification/"
DECISION_ID_PREFIX = "urdi:decision/"


def _premise_to_jsonld(premise: Premise) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@type": "urd:Premise",
        "urd:usesTerm": {"@id": premise.term},
        "schema:name": premise.label,
        "urd:satisfied": premise.satisfied,
    }
    if premise.subject:
        node["urd:constrains"] = {"@id": premise.subject}
    if premise.actual is not None:
        node["urd:actualValue"] = premise.actual
    if premise.comparator is not None:
        node["urd:comparator"] = premise.comparator
    if premise.expected is not None:
        node["urd:expectedValue"] = premise.expected
    return node


def decision_to_jsonld(
    decision: DecisionResult,
    student: Student | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """DecisionResult を urd:DecisionResult ノードへ変換する。"""
    node: dict[str, Any] = {
        "@type": "urd:DecisionResult",
        "urd:status": decision.status.value,
        "schema:text": decision.message,
    }
    if node_id:
        node["@id"] = node_id
    if student and student.iri:
        node["schema:about"] = {"@id": student.iri}
    if decision.rule_ids:
        node["urd:appliedRule"] = [
            {"@id": f"{RULE_ID_PREFIX}{rule_id}"} for rule_id in decision.rule_ids
        ]
    legislation = [
        {"@id": reference.legislation_iri}
        for reference in decision.rule_references
        if reference.legislation_iri
    ]
    if legislation:
        node["urd:basedOnLegislation"] = legislation
    if decision.ontology_terms:
        node["urd:usesTerm"] = [{"@id": term} for term in decision.ontology_terms]
    if decision.horn_clause:
        node["urd:hornClause"] = decision.horn_clause
    if decision.calculation:
        node["urd:calculation"] = decision.calculation
    if decision.unmet_conditions:
        node["urd:unmetCondition"] = list(decision.unmet_conditions)
    if decision.premises:
        node["urd:premise"] = [_premise_to_jsonld(premise) for premise in decision.premises]
    return node


def notification_to_jsonld(notification: Notification, student: Student) -> dict[str, Any]:
    """Notification を schema:Message ノードへ変換する。"""
    node: dict[str, Any] = {
        "@id": f"{NOTIFICATION_ID_PREFIX}{student.student_id}/{notification.notification_id}",
        "@type": "schema:Message",
        "schema:name": notification.title,
        "schema:text": notification.message,
        "urd:notificationLevel": notification.level,
        "urd:decision": decision_to_jsonld(notification.decision, student),
    }
    if student.iri:
        node["schema:recipient"] = {"@id": student.iri}
    return node


def rule_to_jsonld(rule: Rule, graph: KnowledgeGraph) -> dict[str, Any]:
    """ルール1件を urd:Rule ノードへ変換し、値をオントロジー用語で表現する。"""
    ontology = RULE_TYPE_ONTOLOGY[rule.rule_type]
    node: dict[str, Any] = {
        "@id": f"{RULE_ID_PREFIX}{rule.rule_id}",
        "@type": "urd:Rule",
        "schema:name": rule.title,
        "schema:description": rule.source,
        "urd:hornClause": ontology.horn_clause,
    }
    subject = getattr(rule, "subject", None)
    if subject:
        node["urd:constrains"] = {"@id": str(subject)}
    legislation = getattr(rule, "legislation", None)
    if legislation:
        node["urd:basedOnLegislation"] = {"@id": str(legislation)}
    node.update(_rule_values(rule, ontology, graph))
    return node


def _rule_values(rule: Rule, ontology: Any, graph: KnowledgeGraph) -> dict[str, Any]:
    """ルールの設定値を、対応するオントロジー用語のキーで返す。"""
    values: dict[str, Any] = {}
    for yaml_key, term in ontology.rule_terms.items():
        raw = getattr(rule, yaml_key, None)
        if raw is None:
            continue
        if yaml_key == "required_courses":
            values[term] = [
                {"@id": graph.course(code).iri}
                for code in raw
                if graph.has_course(code)
            ]
        elif yaml_key == "deadline":
            values[term] = {"@value": str(raw), "@type": "xsd:date"}
        else:
            values[term] = raw
    return values


def effective_graph(graph: KnowledgeGraph, rules: list[Rule]) -> dict[str, Any]:
    """ナレッジグラフにルール由来の値を重ねた「実効グラフ」を返す。

    ルール値はグラフ本体には書かない（rules.yaml が唯一の情報源）。
    外部へ渡すときだけ、ここで対象ノードへ射影して1つの文書にまとめる。
    """
    nodes: dict[str, dict[str, Any]] = {
        node_id: dict(node) for node_id, node in graph.nodes.items()
    }
    rule_nodes: list[dict[str, Any]] = []
    for rule in rules:
        rule_node = rule_to_jsonld(rule, graph)
        rule_nodes.append(rule_node)
        subject = getattr(rule, "subject", None)
        if not subject or subject not in nodes:
            continue
        ontology = RULE_TYPE_ONTOLOGY[rule.rule_type]
        for term, value in _rule_values(rule, ontology, graph).items():
            nodes[subject][term] = value
    return {
        "@context": dict(JSONLD_CONTEXT),
        "@graph": [*nodes.values(), *rule_nodes],
    }


def answer_to_jsonld(
    question: str,
    intent: str,
    decision: DecisionResult,
    student: Student,
) -> dict[str, Any]:
    """質問アシスタントの1回分の応答をJSON-LD文書として返す。"""
    return {
        "@context": dict(JSONLD_CONTEXT),
        "urd:question": question,
        "urd:detectedIntent": intent,
        "urd:decision": decision_to_jsonld(
            decision, student, node_id=f"{DECISION_ID_PREFIX}{student.student_id}/{intent}"
        ),
    }
