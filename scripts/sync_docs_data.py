"""docs/ 配下のページに埋め込むデータブロックを、data/ と src/ から再生成する。

GitHub Pages 版はサーバーを持たないため、ナレッジグラフ・ルール・語彙定義を
HTML内へ埋め込む。そのままだと Python 側と二重管理になるので、埋め込み部分は
必ずこのスクリプトで生成し、``tests/test_docs_mirror.py`` が同期を検証する。

    python scripts/sync_docs_data.py

判定ロジック（JavaScript）は引き続き手で移植する必要がある。
このスクリプトが同期させるのは「語彙とデータ」であって「処理」ではない。
処理の一致は tests/test_docs_mirror.py が Node.js で実行して確認する。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.graph_document import GraphDocument  # noqa: E402
from src.ontology import (  # noqa: E402
    CCSO_VERSION,
    CONDITION_TERM_LABELS,
    CONDITION_TERMS,
    CROSSWALK,
    GRAPH_PATH,
    JSONLD_CONTEXT,
    LOCAL_TERM_DEFINITIONS,
    RULE_TYPE_ONTOLOGY,
    SCHEMA_ORG_VERSION,
)

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


def ontology_graph_block() -> str:
    document = GraphDocument(GRAPH_PATH)
    return json.dumps(document.to_dict(), ensure_ascii=False, indent=1)


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
