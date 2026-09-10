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
