"""docs/examples.html で書き出した確認結果（JSON）を data/examples.yaml の review へ取り込む。

    python scripts/import_reviews.py example_reviews.json
    python scripts/import_reviews.py example_reviews.json --examples data/examples.yaml

JSON の形式（examples.html の「確認結果を書き出す」が作るもの）::

    {"examples": [{"id": "FY-AI-01", "verdict": "disagree", "correct_status": "unknown",
                   "note": "...", "reviewer": "...", "reviewed_on": "2026-10-09"}]}

書き戻しは ExampleRepository.save_reviews が行う（画面の「確認結果を保存」と同じ検証と書き出し）。
未知の id、不明な verdict、verdict と correct_status の不整合があれば、何も書かずにエラーで終わる。
取り込んだら次を実行して、Pages 版の埋め込みとテストを更新する::

    python scripts/sync_docs_data.py && python -m pytest
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.examples import ExampleRepository, ExampleValidationError  # noqa: E402
from src.knowledge_graph import KnowledgeGraph  # noqa: E402

EXAMPLES_PATH = ROOT / "data" / "examples.yaml"
GRAPH_PATH = ROOT / "data" / "university_graph.jsonld"

REVIEW_FIELDS = ("verdict", "correct_status", "note", "reviewer", "reviewed_on")


def parse_reviews(payload: Any) -> dict[str, dict[str, Any]]:
    """書き出した JSON → 例題 id → review。形式が違えば ExampleValidationError。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("examples"), list):
        raise ExampleValidationError("JSON に examples の一覧がありません。")
    reviews: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(payload["examples"]):
        if not isinstance(entry, dict):
            raise ExampleValidationError(f"{index + 1}件目が辞書ではありません。")
        example_id = entry.get("id")
        if not isinstance(example_id, str) or not example_id:
            raise ExampleValidationError(f"{index + 1}件目に id がありません。")
        if example_id in reviews:
            raise ExampleValidationError(f"同じ例題が2回あります: {example_id}")
        unknown = set(entry) - {"id", *REVIEW_FIELDS}
        if unknown:
            raise ExampleValidationError(f"{example_id}: 不明な項目があります: {sorted(unknown)}")
        review = {key: entry.get(key) for key in REVIEW_FIELDS}
        if review["reviewed_on"] is not None:
            # 画面の保存と同じく、YAML には日付として書く
            try:
                review["reviewed_on"] = date.fromisoformat(str(review["reviewed_on"]))
            except ValueError as exc:
                raise ExampleValidationError(
                    f"{example_id}: reviewed_on は YYYY-MM-DD です: {review['reviewed_on']}"
                ) from exc
        reviews[example_id] = review
    return reviews


def import_reviews(
    reviews_path: str | Path,
    examples_path: str | Path = EXAMPLES_PATH,
    graph_path: str | Path = GRAPH_PATH,
) -> int:
    """取り込んだ件数を返す。検証に通らなければ何も書かない。"""
    try:
        payload = json.loads(Path(reviews_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExampleValidationError(f"JSON として読めません: {exc}") from exc
    reviews = parse_reviews(payload)
    ExampleRepository(examples_path, KnowledgeGraph(graph_path)).save_reviews(reviews)
    return len(reviews)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reviews", help="examples.html で書き出した JSON")
    parser.add_argument("--examples", default=str(EXAMPLES_PATH), help="取り込み先の examples.yaml")
    parser.add_argument("--graph", default=str(GRAPH_PATH), help="科目コードの検証に使うナレッジグラフ")
    args = parser.parse_args(argv)
    try:
        count = import_reviews(args.reviews, args.examples, args.graph)
    except (ExampleValidationError, OSError) as exc:
        print(f"取り込めませんでした：{exc}", file=sys.stderr)
        return 1
    print(f"{count}件の確認結果を {args.examples} に取り込みました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
