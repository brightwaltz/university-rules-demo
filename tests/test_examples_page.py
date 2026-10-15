"""Pages 版の例題チェック（docs/examples.html）と、確認結果の取り込み（scripts/import_reviews.py）。

ページは判定を実行せず、sync_docs_data.py が Python の RuleEngine で計算した結果を埋め込む。
そのため、埋め込みが examples.yaml・評価結果と一致していることを確かめる。
"""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from src.examples import ExampleRepository, ExampleValidationError, evaluate_example, matches
from src.knowledge_graph import KnowledgeGraph

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "examples.html"
HORN_JS = ROOT / "docs" / "assets" / "horn.js"
EXAMPLES_PATH = ROOT / "data" / "examples.yaml"
RULES_PATH = ROOT / "data" / "rules.yaml"
GRAPH_PATH = ROOT / "data" / "university_graph.jsonld"


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sync():
    return _load_script("sync_docs_data", "sync_docs_data.py")


@pytest.fixture(scope="module")
def importer():
    return _load_script("import_reviews", "import_reviews.py")


@pytest.fixture(scope="module")
def embedded(sync) -> list[dict]:
    html = PAGE.read_text(encoding="utf-8")
    block = next(m for m in sync.BLOCK.finditer(html) if m.group("name") == "examples")
    return json.loads(block.group("body"))


# --- 埋め込み -------------------------------------------------------------------


def test_examples_page_is_in_sync(sync):
    """`python scripts/sync_docs_data.py` を流しても examples.html が変化しないこと。"""
    html = PAGE.read_text(encoding="utf-8")
    assert sync.render(html, sync.PAGES["examples.html"]) == html, (
        "docs/examples.html の埋め込みデータが古くなっています。"
        "`python scripts/sync_docs_data.py` を実行してください。"
    )


def test_embedded_examples_follow_the_yaml_and_the_engine(embedded):
    graph = KnowledgeGraph(GRAPH_PATH)
    examples = ExampleRepository(EXAMPLES_PATH, graph).load()
    assert [item["id"] for item in embedded] == [example["id"] for example in examples]
    for item, example in zip(embedded, examples):
        outcome = evaluate_example(example, RULES_PATH, graph)
        assert item["expected"]["status"] == example["expected"]["status"], item["id"]
        assert item["ai_note"] == example.get("ai_note"), item["id"]
        assert item["review"]["verdict"] == example["review"]["verdict"], item["id"]
        assert item["outcome"]["status"] == outcome.status, item["id"]
        assert item["outcome"]["proof"] == outcome.proof, item["id"]
        assert item["matches"] == matches(example, outcome), item["id"]
        assert item["legislation"] and all(article["text"] for article in item["legislation"]), item["id"]


def test_examples_page_uses_the_site_layout():
    html = PAGE.read_text(encoding="utf-8")
    assert 'href="assets/site.css"' in html
    assert "site-nav" in html
    assert "<style>" not in html
    assert '<script src="assets/horn.js"></script>' in html


# --- 描画（Node.js） --------------------------------------------------------------


def _run_render(*extra: str) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js が無いため 例題チェック画面の描画確認をスキップします。")
    completed = subprocess.run(
        [node, str(ROOT / "tests" / "js" / "check_examples_render.mjs"), str(PAGE), *extra],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _assert_steps(steps: list[dict]) -> None:
    failed = [step for step in steps if not step["ok"]]
    assert not failed, "\n".join(f"{s['step']}: {s['detail']}" for s in failed)


def test_examples_page_renders_and_exports_with_the_fallback_proof():
    """horn.js なし（ページ内の簡易版の証明木）で、描画・絞り込み・入力・書き出しが通ること。"""
    steps = _run_render()
    _assert_steps(steps)
    assert steps[0]["detail"] == len(yaml.safe_load(EXAMPLES_PATH.read_text(encoding="utf-8"))["examples"])
    exported = json.loads(next(s for s in steps if s["step"] == "書き出し")["detail"])
    for entry in exported["examples"]:
        assert set(entry) == {"id", "verdict", "correct_status", "note", "reviewer", "reviewed_on"}
    assert exported["examples"][1]["note"] == "確認した"


def test_examples_page_renders_with_horn_js():
    if not HORN_JS.exists():
        pytest.skip("docs/assets/horn.js がまだありません。")
    _assert_steps(_run_render(str(HORN_JS)))


# --- 確認結果の取り込み -----------------------------------------------------------


@pytest.fixture
def examples_copy(tmp_path) -> Path:
    path = tmp_path / "examples.yaml"
    shutil.copyfile(EXAMPLES_PATH, path)
    return path


def _write_reviews(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "example_reviews.json"
    path.write_text(json.dumps({"examples": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def test_import_reviews_writes_only_the_given_examples(importer, examples_copy, tmp_path):
    before = yaml.safe_load(examples_copy.read_text(encoding="utf-8"))["examples"]
    reviews = _write_reviews(
        tmp_path,
        [
            {"id": "FY-AI-02", "verdict": "disagree", "correct_status": "not_eligible", "note": "編入生も対象",
             "reviewer": "担当者", "reviewed_on": "2026-10-09"},
            {"id": "FY-AI-01", "verdict": "agree", "correct_status": None, "note": None,
             "reviewer": "担当者", "reviewed_on": "2026-10-09"},
        ],
    )
    assert importer.main([str(reviews), "--examples", str(examples_copy)]) == 0

    after = yaml.safe_load(examples_copy.read_text(encoding="utf-8"))["examples"]
    by_id = {example["id"]: example for example in after}
    assert by_id["FY-AI-02"]["review"]["verdict"] == "disagree"
    assert by_id["FY-AI-02"]["review"]["correct_status"] == "not_eligible"
    assert str(by_id["FY-AI-02"]["review"]["reviewed_on"]) == "2026-10-09"
    assert by_id["FY-AI-01"]["review"]["verdict"] == "agree"
    changed = {e["id"] for e, o in zip(after, before) if e != o}
    assert changed == {"FY-AI-01", "FY-AI-02"}
    assert [e["id"] for e in after] == [e["id"] for e in before]


def test_imported_reviews_can_be_embedded_again(importer, sync, examples_copy, tmp_path, monkeypatch):
    """取り込んだ日付（YAML の date）でも、Pages 版の埋め込みを作り直せること。"""
    reviews = _write_reviews(
        tmp_path,
        [{"id": "FY-AI-03", "verdict": "unsure", "correct_status": None, "note": None,
          "reviewer": "担当者", "reviewed_on": "2026-10-09"}],
    )
    assert importer.main([str(reviews), "--examples", str(examples_copy)]) == 0
    data = tmp_path / "root" / "data"
    data.mkdir(parents=True)
    for name in ("university_graph.jsonld", "rules.yaml"):
        shutil.copyfile(ROOT / "data" / name, data / name)
    shutil.copyfile(examples_copy, data / "examples.yaml")
    monkeypatch.setattr(sync, "ROOT", tmp_path / "root")
    items = json.loads(sync.examples_block())
    item = next(item for item in items if item["id"] == "FY-AI-03")
    assert item["review"]["reviewed_on"] == "2026-10-09"


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{"id": "NO-SUCH", "verdict": "agree"}], "例題がありません"),
        ([{"id": "FY-AI-01", "verdict": "disagree", "correct_status": None}], "正しい結論"),
        ([{"id": "FY-AI-01", "verdict": "agree", "correct_status": "eligible"}], "correct_status"),
        ([{"id": "FY-AI-01", "verdict": "maybe"}], "verdict"),
        ([{"id": "FY-AI-01", "verdict": "agree", "reviewed_on": "10/9"}], "reviewed_on"),
        ([{"id": "FY-AI-01", "verdict": "agree", "extra": 1}], "不明な項目"),
        ([{"verdict": "agree"}], "id がありません"),
        ([{"id": "FY-AI-01", "verdict": "agree"}, {"id": "FY-AI-01", "verdict": "agree"}], "2回"),
    ],
)
def test_import_reviews_rejects_inconsistent_input(importer, examples_copy, tmp_path, capsys, entries, message):
    original = examples_copy.read_text(encoding="utf-8")
    reviews = _write_reviews(tmp_path, entries)
    assert importer.main([str(reviews), "--examples", str(examples_copy)]) == 1
    assert message in capsys.readouterr().err
    assert examples_copy.read_text(encoding="utf-8") == original


def test_import_reviews_rejects_a_file_that_is_not_an_export(importer, examples_copy, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ExampleValidationError):
        importer.import_reviews(path, examples_copy)
    path.write_text(json.dumps({"reviews": []}), encoding="utf-8")
    with pytest.raises(ExampleValidationError, match="examples の一覧"):
        importer.import_reviews(path, examples_copy)
