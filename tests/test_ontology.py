"""デモが使う用語が、実在する語彙の用語であることを検証する。

data/schemaorg_terms.json と data/ccso_terms.json は、それぞれの公式配布物から
用語名だけを抜き出して固定したもの。ここで突き合わせることで、
存在しない用語をうっかり書いてしまう事故を防ぐ。
"""

import json
from pathlib import Path

import pytest

from src.ontology import (
    CROSSWALK,
    JSONLD_CONTEXT,
    LOCAL_TERM_DEFINITIONS,
    RULE_TYPE_ONTOLOGY,
    all_curies,
    expand,
)


ROOT = Path(__file__).resolve().parents[1]
VALIDATED_PREFIXES = {"schema", "ccso", "urd"}


@pytest.fixture(scope="module")
def schema_terms() -> set[str]:
    payload = json.loads((ROOT / "data" / "schemaorg_terms.json").read_text(encoding="utf-8"))
    return {
        *payload["classes"],
        *payload["properties"],
        *payload["enumeration_members"],
    }


@pytest.fixture(scope="module")
def ccso_terms() -> set[str]:
    payload = json.loads((ROOT / "data" / "ccso_terms.json").read_text(encoding="utf-8"))
    return {
        *payload["classes"],
        *payload["object_properties"],
        *payload["data_properties"],
    }


def collect_curies(value, found: set[str]) -> set[str]:
    """JSON-LD文書から、述語・型・列挙値として現れるCURIEを集める。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in {"@id", "@type", "@value", "@context", "@graph"} and ":" in key:
                found.add(key)
            if key in {"@type", "@id"}:
                for entry in item if isinstance(item, list) else [item]:
                    if isinstance(entry, str) and ":" in entry:
                        found.add(entry)
            collect_curies(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_curies(item, found)
    return found


def assert_known(curie: str, schema_terms: set[str], ccso_terms: set[str]) -> None:
    prefix, _, local = curie.partition(":")
    assert prefix in JSONLD_CONTEXT, f"未知の接頭辞です: {curie}"
    if prefix not in VALIDATED_PREFIXES:
        return
    if prefix == "schema":
        assert local in schema_terms, f"schema.org に存在しない用語です: {curie}"
    elif prefix == "ccso":
        assert local in ccso_terms, f"CCSO に存在しない用語です: {curie}"
    elif prefix == "urd":
        assert curie in LOCAL_TERM_DEFINITIONS, f"ローカル用語が未定義です: {curie}"


def test_every_curie_used_by_the_engine_exists(schema_terms, ccso_terms):
    for curie in all_curies():
        assert_known(curie, schema_terms, ccso_terms)


def test_every_curie_in_the_knowledge_graph_exists(schema_terms, ccso_terms):
    document = json.loads(
        (ROOT / "data" / "university_graph.jsonld").read_text(encoding="utf-8")
    )
    curies = collect_curies(document["@graph"], set())
    # urdi: はインスタンスのIRIなので語彙の検証対象外。
    for curie in sorted(curies):
        if curie.startswith("urdi:"):
            continue
        assert_known(curie, schema_terms, ccso_terms)


def test_local_terms_are_all_documented():
    for curie, definition in LOCAL_TERM_DEFINITIONS.items():
        assert curie.startswith("urd:")
        assert definition["label"]
        assert definition["comment"]


def test_close_matches_point_at_real_terms(schema_terms, ccso_terms):
    for definition in LOCAL_TERM_DEFINITIONS.values():
        for curie in definition["close_match"]:
            assert_known(curie, schema_terms, ccso_terms)


def test_every_rule_type_declares_a_horn_clause_and_subject():
    for rule_type, ontology in RULE_TYPE_ONTOLOGY.items():
        assert ontology.horn_clause.endswith("."), rule_type
        assert ontology.subject_class.startswith(("schema:", "ccso:", "urd:")), rule_type
        assert ontology.terms(), rule_type


def test_crosswalk_covers_every_local_term():
    """ローカル拡張した用語は、必ず対応表で説明されていること。"""
    described = " ".join(
        f"{row['demo']} {row['schema_org']} {row['ccso']} {row['oloud']}" for row in CROSSWALK
    )
    for curie in ["urd:yearOfStudy", "urd:earnedCredits", "urd:annualCreditLimit", "urd:requiredCourse"]:
        assert curie in described, f"対応表に記載がありません: {curie}"


def test_rule_terms_match_the_keys_actually_used_in_rules_yaml():
    """rule_terms のキーが rules.yaml のキー名とずれていないこと。

    ずれると JSON-LD 出力から値が黙って欠落する。
    """
    import yaml

    document = yaml.safe_load((ROOT / "data" / "rules.yaml").read_text(encoding="utf-8"))
    for rule in document["rules"]:
        ontology = RULE_TYPE_ONTOLOGY[rule["rule_type"]]
        for yaml_key in ontology.rule_terms:
            assert yaml_key in rule, f"{rule['rule_id']} に {yaml_key} がありません"


def test_expand_produces_absolute_iris():
    assert expand("schema:Course") == "https://schema.org/Course"
    assert expand("ccso:hasCompleted") == "https://w3id.org/ccso/ccso#hasCompleted"
    with pytest.raises(ValueError):
        expand("unknown:Thing")
