"""docs/ 配下のページに埋め込むデータブロックを、data/ と src/ から再生成する。

GitHub Pages 版はサーバーを持たないため、ナレッジグラフ・ルール・語彙定義を
HTML内へ埋め込む。そのままだと Python 側と二重管理になるので、埋め込み部分は
必ずこのスクリプトで生成し、``tests/test_docs_mirror.py`` が同期を検証する。

    python scripts/sync_docs_data.py

判定は Horn 節（program_sources）を docs/assets/horn.js の処理系で評価するので、
節そのものはここから埋め込まれる。手で移植しているのは処理系と画面側の組み立てで、
その一致は tests/test_docs_mirror.py が Node.js で実行して確認する。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.examples import (  # noqa: E402
    EXAMPLE_RULE_LABELS,
    EXAMPLE_RULE_TYPES,
    ExampleRepository,
    describe_student,
    effective_expected,
    evaluate_example,
    matches,
)
from src.graph_document import GraphDocument  # noqa: E402
from src.knowledge_graph import KnowledgeGraph  # noqa: E402
from src.ontology import (  # noqa: E402
    CCSO_VERSION,
    CONDITION_TERM_LABELS,
    CONDITION_TERMS,
    CROSSWALK,
    GRAPH,
    GRAPH_PATH,
    JSONLD_CONTEXT,
    LOCAL_TERM_DEFINITIONS,
    RULE_TYPE_ONTOLOGY,
    SCHEMA_ORG_VERSION,
)
from src.rule_repository import RuleRepository  # noqa: E402

DOCS = ROOT / "docs"
BLOCK = re.compile(
    r"(?P<head><!-- BEGIN (?P<name>[a-z-]+) .*?-->\s*<script [^>]*>)"
    r"(?P<body>.*?)"
    r"(?P<tail></script>\s*<!-- END (?P=name) -->)",
    re.DOTALL,
)


def knowledge_graph_block() -> str:
    return (ROOT / "data" / "university_graph.jsonld").read_text(encoding="utf-8").strip()


def default_rules_block() -> str:
    document = yaml.safe_load((ROOT / "data" / "rules.yaml").read_text(encoding="utf-8"))
    settings = dict(document["settings"])
    settings["evaluation_date"] = str(settings["evaluation_date"])
    rules = []
    for rule in document["rules"]:
        normalized = dict(rule)
        if "deadline" in normalized:
            normalized["deadline"] = str(normalized["deadline"])
        rules.append(normalized)
    return json.dumps({"settings": settings, "rules": rules}, ensure_ascii=False, indent=1)


def program_sources() -> list[dict[str, str]]:
    """HORN_PROGRAM の材料。src/ontology.py の _load_horn_program と同じ順・同じ原文。

    Pages 版（docs/assets/horn.js）はこれを parse・combine・check して同じプログラムを作る。
    """
    sources: list[dict[str, str]] = []
    for node in GRAPH.nodes_of_kind("rule-type"):
        if not bool(getattr(node, "executable", True)):
            continue
        sources.append({"origin": node.id, "text": str(getattr(node, "horn_clause"))})
    for node in GRAPH.terms():
        source = getattr(node, "horn_clause", None)
        if source:
            sources.append({"origin": node.id, "text": str(source)})
    return sources


def term_definitions() -> dict[str, dict[str, object]]:
    """読み下しに使う term ノードの情報（CURIE → id・label・reading・variables）。

    variables は定義節（horn_clause）を持つ用語だけ辞書、それ以外は null（src/horn_reading.py と同じ）。
    """
    terms: dict[str, dict[str, object]] = {}
    for node in GRAPH.terms():
        reading = getattr(node, "reading", None)
        terms[str(getattr(node, "curie"))] = {
            "id": node.id,
            "label": node.label,
            "reading": str(reading) if reading else None,
            "variables": (
                dict(getattr(node, "variables", None) or {})
                if getattr(node, "horn_clause", None)
                else None
            ),
        }
    return terms


def ontology_block() -> str:
    payload = {
        "schema_org_version": SCHEMA_ORG_VERSION,
        "ccso_version": CCSO_VERSION,
        "context": JSONLD_CONTEXT,
        "rule_types": {
            rule_type: {
                "label": ontology.label,
                "subject_class": ontology.subject_class,
                "rule_terms": ontology.rule_terms,
                "terms": ontology.terms(),
                "horn_clause": ontology.horn_clause,
                "executable": ontology.executable,
                "goal": ontology.goal,
                "readings": ontology.readings,
                "variables": ontology.variables,
            }
            for rule_type, ontology in RULE_TYPE_ONTOLOGY.items()
        },
        "program_sources": program_sources(),
        "terms": term_definitions(),
        "condition_terms": {
            term: {"type": kind.__name__, "label": CONDITION_TERM_LABELS.get(term, term)}
            for term, (_, kind) in CONDITION_TERMS.items()
        },
        "crosswalk": CROSSWALK,
        "local_terms": LOCAL_TERM_DEFINITIONS,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def ontology_graph_block() -> str:
    document = GraphDocument(GRAPH_PATH)
    return json.dumps(document.to_dict(), ensure_ascii=False, indent=1)


def examples_block() -> str:
    """例題と、それに対するシステムの判定（証明木を含む）。並びと件数は examples.yaml のまま。

    Pages 版は判定を実行しないので、ここで Python の RuleEngine が計算した結果を埋め込む。
    1行に1例題（証明木が大きいため、字下げせずに詰める）。
    """
    graph = KnowledgeGraph(ROOT / "data" / "university_graph.jsonld")
    rules_path = ROOT / "data" / "rules.yaml"
    rules = RuleRepository(rules_path, graph).list_rules()
    items = []
    for example in ExampleRepository(ROOT / "data" / "examples.yaml", graph).load():
        rule_type = str(example["rule_type"])
        outcome = evaluate_example(example, rules_path, graph)
        legislation = []
        for rule in rules:
            if rule.rule_type not in EXAMPLE_RULE_TYPES[rule_type][2]:
                continue
            article = graph.legislation(getattr(rule, "legislation", None))
            if article is not None and article.text:
                legislation.append(
                    {"citation": article.citation, "name": article.name, "text": article.text}
                )
        items.append(
            {
                "id": example["id"],
                "rule_type": rule_type,
                "rule_label": EXAMPLE_RULE_LABELS[rule_type],
                "origin": example["origin"],
                "title": example["title"],
                "description": describe_student(example, graph),
                "expected": {
                    "status": example["expected"]["status"],
                    "reason": example["expected"].get("reason"),
                },
                "ai_note": example.get("ai_note"),
                "review": example["review"],
                "outcome": outcome.model_dump(mode="json"),
                "effective_expected": effective_expected(example),
                "matches": matches(example, outcome),
                "legislation": legislation,
            }
        )
    # reviewed_on は YAML から date で読まれるので default=str で ISO 形式にする
    lines = ",\n".join(json.dumps(item, ensure_ascii=False, default=str) for item in items)
    return "[\n" + lines + "\n]"


#: ページごとの生成ブロック
PAGES: dict[str, dict[str, object]] = {
    "index.html": {
        "knowledge-graph": knowledge_graph_block,
        "default-rules": default_rules_block,
        "ontology": ontology_block,
    },
    "graph.html": {
        "ontology-graph": ontology_graph_block,
    },
    "examples.html": {
        "examples": examples_block,
    },
}


def render(html: str, builders: dict[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        builder = builders.get(match.group("name"))
        if builder is None:
            return match.group(0)
        return f"{match.group('head')}\n{builder()}\n{match.group('tail')}"  # type: ignore[operator]

    return BLOCK.sub(replace, html)


def main() -> int:
    changed: list[str] = []
    for filename, builders in PAGES.items():
        path = DOCS / filename
        if not path.exists():
            print(f"{filename} がありません。", file=sys.stderr)
            return 1
        html = path.read_text(encoding="utf-8")
        names = {match.group("name") for match in BLOCK.finditer(html)}
        missing = set(builders) - names
        if missing:
            print(f"{filename} に生成ブロックがありません: {sorted(missing)}", file=sys.stderr)
            return 1
        updated = render(html, builders)
        if updated != html:
            path.write_text(updated, encoding="utf-8")
            changed.append(f"{filename}（{', '.join(sorted(builders))}）")
    if changed:
        print("更新しました: " + " / ".join(changed))
    else:
        print("docs/ は既に最新です。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
