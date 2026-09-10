"""共通語彙で書いたことの実利を検証する。

デモ本体は JSON-LD を自前の小さなローダーで読むが、同じファイルを
標準的なRDFツール（rdflib）がそのまま解釈でき、SPARQLで問い合わせられる。
これが「別のプログラムとデータを共有できる」という主張の根拠になる。
"""

import json
from pathlib import Path

import pytest
from rdflib import Graph as RDFGraph

from src.jsonld_export import answer_to_jsonld, effective_graph
from src.rule_repository import RuleRepository


ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "data" / "university_graph.jsonld"

# インスタンスのIRIは urdi:course/REQ-A のように階層を持つ。SPARQLの
# 接頭辞付き名前はローカル部に "/" を書けないため、コレクションごとに
# 接頭辞を切って course:REQ-A と書けるようにしている。
PREFIXES = """
PREFIX schema: <https://schema.org/>
PREFIX ccso: <https://w3id.org/ccso/ccso#>
PREFIX urd: <https://brightwaltz.github.io/university-rules-demo/ns#>
PREFIX course: <https://brightwaltz.github.io/university-rules-demo/id/course/>
PREFIX program: <https://brightwaltz.github.io/university-rules-demo/id/program/>
PREFIX rule: <https://brightwaltz.github.io/university-rules-demo/id/rule/>
"""


@pytest.fixture(scope="module")
def rdf_graph() -> RDFGraph:
    return RDFGraph().parse(source=str(GRAPH_PATH), format="json-ld")


def test_rdflib_reads_the_knowledge_graph(rdf_graph):
    assert len(rdf_graph) > 300


def test_sparql_can_sum_earned_credits(rdf_graph):
    rows = list(
        rdf_graph.query(
            PREFIXES
            + """
            SELECT (SUM(?credits) AS ?total) WHERE {
              ?student schema:identifier "S001" ;
                       ccso:hasCompleted ?course .
              ?course schema:numberOfCredits ?credits .
            }
            """
        )
    )
    assert int(rows[0][0]) == 92


def test_sparql_can_find_students_missing_a_required_course(rdf_graph):
    rows = rdf_graph.query(
        PREFIXES
        + """
        SELECT ?id WHERE {
          ?student schema:identifier ?id .
          ?student a schema:Person .
          FILTER NOT EXISTS { ?student ccso:hasCompleted course:REQ-B }
        }
        """
    )
    assert {str(row[0]) for row in rows} == {"S001"}


def test_sparql_can_walk_from_a_rule_to_its_article(graph):
    rules = RuleRepository(ROOT / "data" / "rules.yaml", graph).list_rules()
    document = effective_graph(graph, rules)
    rdf = RDFGraph().parse(data=json.dumps(document), format="json-ld")

    rows = list(
        rdf.query(
            PREFIXES
            + """
            SELECT ?identifier ?parent WHERE {
              rule:RULE-GRAD-001 urd:basedOnLegislation ?article .
              ?article schema:legislationIdentifier ?identifier ;
                       schema:isPartOf ?law .
              ?law schema:name ?parent .
            }
            """
        )
    )
    assert [(str(a), str(b)) for a, b in rows] == [("第32条第1項", "2026年度学則")]


def test_rule_values_are_projected_onto_their_subject(graph):
    rules = RuleRepository(ROOT / "data" / "rules.yaml", graph).list_rules()
    rdf = RDFGraph().parse(data=json.dumps(effective_graph(graph, rules)), format="json-ld")

    assert rdf.query(
        PREFIXES
        + """
        ASK {
          program:engineering-2026 schema:numberOfCredits 124 ;
                                   urd:annualCreditLimit 48 ;
                                   urd:requiredCourse course:REQ-A .
        }
        """
    ).askAnswer


def test_an_answer_document_is_valid_jsonld(engine, eligible_student):
    from src.answer_service import AnswerService
    from src.intent import RuleBasedIntentDetector

    intent, decision = AnswerService(engine, RuleBasedIntentDetector()).answer(
        "卒業できますか？", eligible_student
    )
    document = answer_to_jsonld("卒業できますか？", intent.value, decision, eligible_student)
    rdf = RDFGraph().parse(data=json.dumps(document), format="json-ld")

    assert rdf.query(
        PREFIXES
        + """
        ASK { ?decision a urd:DecisionResult ; urd:status "eligible" . }
        """
    ).askAnswer
