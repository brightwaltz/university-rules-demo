import re
from pathlib import Path

from streamlit.testing.v1 import AppTest


# streamlit の初回インポートは環境によって10秒を超えるため、
# クリーンな環境でも落ちないよう余裕を持たせる。
APP_TIMEOUT = 60


def _app() -> AppTest:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    return AppTest.from_file(app_path, default_timeout=APP_TIMEOUT).run()


def test_streamlit_app_student_switch_and_question_answer():
    app = _app()
    assert not app.exception
    assert len(app.selectbox[0].options) == 3

    app.text_input[0].set_value("あと何単位履修できますか？")
    app.button[0].click().run()
    assert not app.exception
    assert any("あと12単位履修できます" in item.value for item in app.info)

    app.selectbox[0].select("S002 鈴木花子").run()
    assert not app.exception
    assert any("完了" in metric.value for metric in app.metric)


def test_answer_shows_the_ontology_terms_and_horn_clause():
    app = _app()
    app.text_input[0].set_value("卒業できますか？")
    app.button[0].click().run()
    assert not app.exception

    markdown = " ".join(item.value for item in app.markdown)
    assert "ccso:hasCompleted" in markdown
    assert any("eligible_to_graduate" in item.value for item in app.code)


def test_first_year_question_shows_the_reading_and_proof():
    app = _app()
    app.selectbox[0].select("S002 鈴木花子").run()
    app.text_input[0].set_value("1年次の単位要件は満たしていますか？")
    app.button[0].click().run()
    assert not app.exception
    assert any("1年次の修得 28単位／必要 31単位" in item.value for item in app.error)
    assert any("meets_first_year_requirement" in item.value for item in app.code)
    markdown = " ".join(item.value for item in app.markdown)
    assert "鈴木花子が、1年次の修得単位要件を満たしている" in markdown  # 証明木の読み下し
    assert "28が31以上" in markdown



def test_example_section_renders_with_counts():
    from src.examples import ExampleRepository, evaluate_example, is_reviewed, matches
    from src.knowledge_graph import KnowledgeGraph

    root = Path(__file__).resolve().parents[1]
    app = _app()
    assert not app.exception
    assert any("6. 例題で確かめる" in header.value for header in app.header)
    assert any("7. 研究用情報" in header.value for header in app.header)
    labels = {metric.label: metric.value for metric in app.metric}
    for label in ("全件", "システムと一致", "不一致", "確認済み", "未確認"):
        assert re.fullmatch(r"\d+ 件", labels[label]), label
    graph = KnowledgeGraph(root / "data" / "university_graph.jsonld")
    examples = ExampleRepository(root / "data" / "examples.yaml", graph).load()
    assert labels["全件"] == f"{len(examples)} 件"
    # 並びの先頭は「未確認かつ不一致」の例題（あれば）
    first_ids = {
        example["id"]
        for example in examples
        if not is_reviewed(example)
        and not matches(example, evaluate_example(example, root / "data" / "rules.yaml", graph))
    }
    detail = next(item for item in app.selectbox if item.label == "詳しく見る例題")
    if first_ids:
        assert detail.value in first_ids
