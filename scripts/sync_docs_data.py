"""docs/index.html に埋め込むデータブロックを data/ と src/ontology.py から再生成する。

GitHub Pages 版はサーバーを持たないため、ナレッジグラフ・ルール・語彙定義を
HTML内へ埋め込む。そのままだと Python 側と二重管理になるので、埋め込み部分は
必ずこのスクリプトで生成し、``tests/test_docs_mirror.py`` が同期を検証する。

    python scripts/sync_docs_data.py

判定ロジック自体（JavaScript）は引き続き手で移植する必要がある。
このスクリプトが同期させるのは「語彙とデータ」であって「処理」ではない。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ontology import (  # noqa: E402
    CCSO_VERSION,
    CONDITION_TERM_LABELS,
    CONDITION_TERMS,
    CROSSWALK,
    JSONLD_CONTEXT,
    LOCAL_TERM_DEFINITIONS,
    RULE_TYPE_ONTOLOGY,
    SCHEMA_ORG_VERSION,
)

DOCS = ROOT / "docs" / "index.html"
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
            }
            for rule_type, ontology in RULE_TYPE_ONTOLOGY.items()
        },
        "condition_terms": {
            term: {"type": kind.__name__, "label": CONDITION_TERM_LABELS.get(term, term)}
            for term, (_, kind) in CONDITION_TERMS.items()
        },
        "crosswalk": CROSSWALK,
        "local_terms": LOCAL_TERM_DEFINITIONS,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


BUILDERS = {
    "knowledge-graph": knowledge_graph_block,
    "default-rules": default_rules_block,
    "ontology": ontology_block,
}


def render(html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        builder = BUILDERS.get(name)
        if builder is None:
            return match.group(0)
        return f"{match.group('head')}\n{builder()}\n{match.group('tail')}"

    return BLOCK.sub(replace, html)


def main() -> int:
    html = DOCS.read_text(encoding="utf-8")
    names = {match.group("name") for match in BLOCK.finditer(html)}
    missing = set(BUILDERS) - names
    if missing:
        print(f"docs/index.html に生成ブロックがありません: {sorted(missing)}", file=sys.stderr)
        return 1
    updated = render(html)
    if updated == html:
        print("docs/index.html は既に最新です。")
        return 0
    DOCS.write_text(updated, encoding="utf-8")
    print(f"docs/index.html を更新しました（{', '.join(sorted(names))}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
