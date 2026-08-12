from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_app_student_switch_and_question_answer():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=10).run()
    assert not app.exception
    assert len(app.selectbox[0].options) == 3

    app.text_input[0].set_value("あと何単位履修できますか？")
    app.button[0].click().run()
    assert not app.exception
    assert any("あと12単位履修できます" in item.value for item in app.info)

    app.selectbox[0].select("S002 鈴木花子").run()
    assert not app.exception
    assert any("完了" in metric.value for metric in app.metric)
