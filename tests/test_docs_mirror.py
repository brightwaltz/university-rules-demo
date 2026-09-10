"""GitHub Pages 版（docs/index.html）が Python 実装と食い違わないことを検証する。

Pages 版はサーバーを持てないため、判定ロジックを JavaScript で二重に実装している。
放置すると必ず乖離するので、次の2点を機械的に確認する。

1. 埋め込みデータ（グラフ・ルール・語彙）が data/ と src/ontology.py と一致すること
2. JavaScript の判定結果が Python の判定結果と一致すること（Node.js で実行して比較）
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.answer_service import AnswerService
from src.intent import RuleBasedIntentDetector
from src.notification import NotificationService


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "index.html"
HARNESS = ROOT / "tests" / "js" / "check_mirror.mjs"

QUESTIONS = [
    "履修登録はいつまで？",
    "あと何単位履修できますか？",
    "年間何単位まで履修できますか？",
    "私は卒業要件を満たしていますか？",
    "卒業に必要な単位数は？",
    "必修科目は足りていますか？",
    "卒業研究を履修できますか？",
    "学食のおすすめは？",
]


def _sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_docs_data", ROOT / "scripts" / "sync_docs_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_embedded_data_is_in_sync_with_the_python_sources():
    """`python scripts/sync_docs_data.py` を流しても変化しないこと。"""
    sync = _sync_module()
    html = DOCS.read_text(encoding="utf-8")
    assert sync.render(html) == html, (
        "docs/index.html の埋め込みデータが古くなっています。"
        "`python scripts/sync_docs_data.py` を実行してください。"
    )


def test_embedded_graph_equals_the_data_file():
    html = DOCS.read_text(encoding="utf-8")
    start = html.index('<script type="application/ld+json" id="knowledge-graph">')
    body = html[html.index(">", start) + 1 : html.index("</script>", start)]
    assert json.loads(body) == json.loads(
        (ROOT / "data" / "university_graph.jsonld").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def javascript_results(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js が無いため JavaScript 側の照合をスキップします。")
    questions_path = tmp_path_factory.mktemp("mirror") / "questions.json"
    questions_path.write_text(json.dumps(QUESTIONS, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [node, str(HARNESS), str(DOCS), str(questions_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        pytest.fail(f"docs/index.html のJavaScript実行に失敗しました:\n{completed.stderr}")
    return json.loads(completed.stdout)


def test_javascript_projects_the_same_students(javascript_results, graph):
    python_students = {student.student_id: student for student in graph.students()}
    assert set(javascript_results) == set(python_students)
    for student_id, payload in javascript_results.items():
        expected = python_students[student_id].model_dump(exclude={"iri"})
        assert payload["projection"] == expected, student_id


def test_javascript_answers_match_the_rule_engine(javascript_results, engine, graph):
    service = AnswerService(engine, RuleBasedIntentDetector())
    for student in graph.students():
        answers = {row["question"]: row for row in javascript_results[student.student_id]["answers"]}
        for question in QUESTIONS:
            intent, decision = service.answer(question, student)
            actual = answers[question]
            context = f"{student.student_id} / {question}"
            assert actual["intent"] == intent.value, context
            assert actual["status"] == decision.status.value, context
            assert actual["message"] == decision.message, context
            assert actual["unmet"] == decision.unmet_conditions, context
            assert actual["terms"] == decision.ontology_terms, context
            assert actual["horn_clause"] == decision.horn_clause, context


def test_javascript_notifications_match_the_notification_service(javascript_results, engine, graph):
    service = NotificationService(engine)
    for student in graph.students():
        expected = [
            {"level": notice.level, "title": notice.title, "message": notice.message}
            for notice in service.generate(student)
        ]
        assert javascript_results[student.student_id]["notifications"] == expected, student.student_id


def test_python_version_is_recent_enough():
    """README が要求する Python 3.11 以上で動かしていること。"""
    assert sys.version_info >= (3, 11)
