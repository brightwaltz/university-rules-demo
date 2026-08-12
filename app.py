from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from src.answer_service import AnswerService
from src.intent import RuleBasedIntentDetector
from src.models import DecisionResult, DecisionStatus, Student
from src.notification import NotificationService
from src.rule_engine import RuleEngine
from src.rule_repository import RULE_TYPE_LABELS, RuleRepository, RuleValidationError


BASE_DIR = Path(__file__).resolve().parent


def build_services() -> tuple[RuleEngine, AnswerService, NotificationService]:
    engine = RuleEngine(BASE_DIR / "data" / "rules.yaml")
    answer_service = AnswerService(engine, RuleBasedIntentDetector())
    return engine, answer_service, NotificationService(engine)


@st.cache_data
def load_students() -> list[Student]:
    raw = json.loads((BASE_DIR / "data" / "students.json").read_text(encoding="utf-8"))
    return [Student.model_validate(item) for item in raw]


def show_decision_basis(result: DecisionResult) -> None:
    st.markdown("#### 判定根拠")
    if not result.rule_references:
        st.caption("適用できる登録済みルールはありません。")
        return

    for reference in result.rule_references:
        st.markdown(f"- **{reference.rule_id}** — {reference.title}")
        st.markdown(f"  - 出典：{reference.source}")

    if result.facts:
        st.markdown("**判定に使用した事実**")
        for key, value in result.facts.items():
            display = "はい" if value is True else "いいえ" if value is False else value
            st.markdown(f"- `{key}`: {display}")
    if result.calculation:
        st.markdown(f"**計算式：** `{result.calculation}`")
    if result.unmet_conditions:
        st.markdown("**満たしていない条件**")
        for condition in result.unmet_conditions:
            st.markdown(f"- {condition}")


def show_status_answer(result: DecisionResult) -> None:
    if result.status == DecisionStatus.ELIGIBLE:
        st.success(result.message, icon="✅")
    elif result.status == DecisionStatus.NOT_ELIGIBLE:
        st.error(result.message, icon="❌")
    elif result.status == DecisionStatus.UNKNOWN:
        st.warning(result.message, icon="⚠️")
    else:
        st.info(result.message, icon="ℹ️")


def _records_from_editor(value: Any) -> list[dict[str, Any]]:
    records = value.to_dict("records") if hasattr(value, "to_dict") else list(value)
    return [
        {key: item for key, item in row.items() if item is not None and str(item).strip()}
        for row in records
        if any(item is not None and str(item).strip() for item in row.values())
    ]


def rule_form_fields(
    prefix: str, rule_type: str, defaults: dict[str, Any]
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rule_id": st.text_input(
            "ルールID",
            value=str(defaults.get("rule_id", "")),
            help="例：RULE-NOTICE-001（半角英大文字・数字・ハイフン）",
            key=f"{prefix}_rule_id",
        ),
        "rule_type": rule_type,
        "title": st.text_input(
            "ルール名", value=str(defaults.get("title", "")), key=f"{prefix}_title"
        ),
        "source": st.text_input(
            "根拠・出典", value=str(defaults.get("source", "")), key=f"{prefix}_source"
        ),
    }

    if rule_type == "registration_deadline":
        payload["academic_year"] = st.number_input(
            "年度", min_value=1, value=int(defaults.get("academic_year", 2026)), key=f"{prefix}_year"
        )
        payload["semester"] = st.selectbox(
            "学期",
            options=["first", "second"],
            index=0 if defaults.get("semester", "first") == "first" else 1,
            format_func=lambda value: "前期" if value == "first" else "後期",
            key=f"{prefix}_semester",
        )
        deadline = defaults.get("deadline", date(2026, 4, 15))
        if isinstance(deadline, str):
            deadline = date.fromisoformat(deadline)
        payload["deadline"] = st.date_input("登録期限", value=deadline, key=f"{prefix}_deadline")
    elif rule_type == "annual_credit_limit":
        payload["max_credits"] = st.number_input(
            "年間履修上限（単位）",
            min_value=1,
            value=int(defaults.get("max_credits", 48)),
            key=f"{prefix}_max_credits",
        )
    elif rule_type == "graduation_credit_requirement":
        payload["required_credits"] = st.number_input(
            "卒業必要単位数",
            min_value=1,
            value=int(defaults.get("required_credits", 124)),
            key=f"{prefix}_required_credits",
        )
    elif rule_type == "graduation_required_courses":
        payload["required_courses"] = st.multiselect(
            "卒業必修科目",
            options=["required_a", "required_b"],
            default=list(defaults.get("required_courses", ["required_a", "required_b"])),
            format_func=lambda value: "必修A" if value == "required_a" else "必修B",
            key=f"{prefix}_grad_courses",
        )
    elif rule_type == "thesis_eligibility":
        payload["minimum_grade"] = st.number_input(
            "最低学年",
            min_value=1,
            value=int(defaults.get("minimum_grade", 4)),
            key=f"{prefix}_minimum_grade",
        )
        payload["minimum_earned_credits"] = st.number_input(
            "最低修得単位数",
            min_value=0,
            value=int(defaults.get("minimum_earned_credits", 100)),
            key=f"{prefix}_minimum_credits",
        )
        payload["required_courses"] = st.multiselect(
            "必要な必修科目",
            options=["required_a", "required_b"],
            default=list(defaults.get("required_courses", ["required_a", "required_b"])),
            format_func=lambda value: "必修A" if value == "required_a" else "必修B",
            key=f"{prefix}_thesis_courses",
        )
    elif rule_type == "personalized_notification":
        payload["enabled"] = st.checkbox(
            "この通知ルールを有効にする",
            value=bool(defaults.get("enabled", True)),
            key=f"{prefix}_enabled",
        )
        payload["message"] = st.text_area(
            "通知本文", value=str(defaults.get("message", "")), key=f"{prefix}_message"
        )
        levels = ["info", "success", "warning", "error"]
        current_level = str(defaults.get("level", "info"))
        payload["level"] = st.selectbox(
            "通知の種類",
            options=levels,
            index=levels.index(current_level) if current_level in levels else 0,
            format_func={
                "info": "情報",
                "success": "達成・完了",
                "warning": "注意",
                "error": "重要",
            }.get,
            key=f"{prefix}_level",
        )
        st.caption("すべての条件を満たした学生にだけ通知します。行は追加・削除できます。")
        raw_conditions = defaults.get(
            "conditions", [{"field": "grade", "operator": ">=", "value": 4}]
        )
        default_conditions = [
            {
                **condition,
                "value": (
                    "true"
                    if condition.get("value") is True
                    else "false"
                    if condition.get("value") is False
                    else str(condition.get("value", ""))
                ),
            }
            for condition in raw_conditions
        ]
        edited_conditions = st.data_editor(
            default_conditions,
            num_rows="dynamic",
            hide_index=True,
            column_config={
                "field": st.column_config.SelectboxColumn(
                    "学生属性",
                    options=[
                        "grade",
                        "earned_credits",
                        "current_registered_credits",
                        "required_a",
                        "required_b",
                        "registration_completed",
                    ],
                    required=True,
                ),
                "operator": st.column_config.SelectboxColumn(
                    "比較", options=["==", "!=", ">=", "<=", ">", "<"], required=True
                ),
                "value": st.column_config.TextColumn(
                    "条件値", help="数値、または true / false（はい / いいえ）", required=True
                ),
            },
            key=f"{prefix}_conditions",
        )
        payload["conditions"] = _records_from_editor(edited_conditions)
    return payload


def show_rule_manager(repository: RuleRepository) -> None:
    st.header("4. ルール管理")
    st.caption("変更は data/rules.yaml に保存され、直後の回答・通知から反映されます。")

    flash = st.session_state.pop("rule_flash", None)
    if flash:
        st.success(flash)

    rules = repository.list_rules()
    st.dataframe(
        [
            {
                "ルールID": rule.rule_id,
                "種別": RULE_TYPE_LABELS.get(rule.rule_type, rule.rule_type),
                "ルール名": rule.title,
                "根拠・出典": rule.source,
            }
            for rule in rules
        ],
        width="stretch",
        hide_index=True,
    )

    add_tab, edit_tab = st.tabs(["ルールを追加", "編集・削除"])
    with add_tab:
        registered_types = {rule.rule_type for rule in rules}
        available_add_types = ["personalized_notification"] + [
            rule_type
            for rule_type in RULE_TYPE_LABELS
            if rule_type != "personalized_notification" and rule_type not in registered_types
        ]
        add_type = st.selectbox(
            "追加するルール種別",
            options=available_add_types,
            format_func=RULE_TYPE_LABELS.get,
            key="add_rule_type",
        )
        existing_ids = {rule.rule_id for rule in rules}
        new_index = 1
        while f"RULE-NOTICE-{new_index:03d}" in existing_ids:
            new_index += 1
        defaults = {
            "rule_id": (
                f"RULE-NOTICE-{new_index:03d}"
                if add_type == "personalized_notification"
                else "RULE-NEW-001"
            ),
            "title": "",
            "source": "管理画面で追加",
        }
        with st.form(f"add_rule_form_{add_type}"):
            payload = rule_form_fields("add", add_type, defaults)
            add_submitted = st.form_submit_button("ルールを追加", type="primary")
        if add_submitted:
            try:
                created = repository.create(payload)
                st.session_state["rule_flash"] = f"{created.rule_id} を追加しました。"
                st.rerun()
            except (RuleValidationError, OSError) as exc:
                st.error(f"追加できませんでした：{exc}")

    with edit_tab:
        if not rules:
            st.info("編集できるルールがありません。")
            return
        selected_rule_id = st.selectbox(
            "編集するルール", options=[rule.rule_id for rule in rules], key="edit_rule_id"
        )
        selected_rule = next(rule for rule in rules if rule.rule_id == selected_rule_id)
        selected_defaults = selected_rule.model_dump(mode="python")
        st.caption(f"種別：{RULE_TYPE_LABELS.get(selected_rule.rule_type, selected_rule.rule_type)}")
        with st.form(f"edit_rule_form_{selected_rule_id}"):
            updated_payload = rule_form_fields(
                f"edit_{selected_rule_id}", selected_rule.rule_type, selected_defaults
            )
            edit_submitted = st.form_submit_button("変更を保存", type="primary")
        if edit_submitted:
            try:
                updated = repository.update(selected_rule_id, updated_payload)
                st.session_state["rule_flash"] = f"{updated.rule_id} を更新しました。"
                st.rerun()
            except (RuleValidationError, OSError) as exc:
                st.error(f"更新できませんでした：{exc}")

        st.divider()
        confirm_delete = st.checkbox(
            f"{selected_rule_id} を削除することを確認しました",
            key=f"delete_confirm_{selected_rule_id}",
        )
        if st.button(
            "このルールを削除",
            disabled=not confirm_delete,
            key=f"delete_{selected_rule_id}",
        ):
            try:
                deleted = repository.delete(selected_rule_id)
                st.session_state["rule_flash"] = f"{deleted.rule_id} を削除しました。"
                st.rerun()
            except (KeyError, OSError) as exc:
                st.error(f"削除できませんでした：{exc}")


st.set_page_config(
    page_title="大学 Rules as Code アシスタント",
    page_icon="🎓",
    layout="wide",
)
st.markdown(
    """
    <style>
      .block-container {max-width: 1120px; padding-top: 2rem;}
      [data-testid="stMetric"] {
        background: var(--secondary-background-color);
        color: var(--text-color);
        border: 1px solid color-mix(in srgb, var(--text-color) 18%, transparent);
        border-radius: 12px; padding: 12px 16px;
      }
      .student-card {
        padding: 16px 18px; border-radius: 12px;
        background: var(--secondary-background-color);
        color: var(--text-color);
        border: 1px solid color-mix(in srgb, var(--text-color) 18%, transparent);
        margin: 4px 0 16px;
      }
      .system-flow {
        text-align: center; line-height: 1.8; padding: 12px;
        background: var(--secondary-background-color);
        color: var(--text-color);
        border: 1px solid color-mix(in srgb, var(--text-color) 18%, transparent);
        border-radius: 10px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

rules_path = BASE_DIR / "data" / "rules.yaml"
repository = RuleRepository(rules_path)
engine, answer_service, notification_service = build_services()
students = load_students()
student_by_label = {student.display_name: student for student in students}

with st.sidebar:
    st.header("このシステムの仕組み")
    st.markdown(
        """
        <div class="system-flow">
          質問<br>↓<br>Intent Detection<br>↓<br>
          Rules as Code ＋ 学生データ<br>↓<br>
          決定論的判定<br>↓<br>回答・通知
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "生成AIに大学制度上の判断を直接生成させるのではなく、"
        "Rules as Codeによる決定論的判定結果を回答に利用しています。"
    )
    st.divider()
    st.write(f"デモ基準日：{engine.evaluation_date:%Y年%m月%d日}")
    st.caption("APIキー不要 / LLM未使用")

st.title("大学 Rules as Code アシスタント")
st.caption("学生情報と大学規則を組み合わせ、根拠に基づいて回答・通知する研究用プロトタイプ")

st.header("1. 学生選択")
selected_label = st.selectbox("対象の学生", options=list(student_by_label))
student = student_by_label[selected_label]

if st.session_state.get("selected_student_id") != student.student_id:
    st.session_state["selected_student_id"] = student.student_id
    st.session_state.pop("last_answer", None)

st.markdown(
    f"""
    <div class="student-card">
      <strong>{student.name}</strong>（{student.student_id}）&nbsp; | &nbsp;
      {student.faculty} {student.grade}年
    </div>
    """,
    unsafe_allow_html=True,
)
metric_cols = st.columns(4)
metric_cols[0].metric("修得単位", f"{student.earned_credits} 単位")
metric_cols[1].metric("現在履修登録", f"{student.current_registered_credits} 単位")
metric_cols[2].metric("必修A / 必修B", f"{'済' if student.required_a else '未'} / {'済' if student.required_b else '未'}")
metric_cols[3].metric("履修登録", "完了" if student.registration_completed else "未完了")

st.header("2. あなたへのお知らせ")
notifications = notification_service.generate(student)
if not notifications:
    st.info("現在、あなたへの個別のお知らせはありません。")
for notice in notifications:
    renderer = {
        "success": st.success,
        "warning": st.warning,
        "error": st.error,
        "info": st.info,
    }.get(notice.level, st.info)
    renderer(f"**{notice.title}**\n\n{notice.message}")
    with st.expander(f"{notice.title}の判定根拠"):
        show_decision_basis(notice.decision)

st.header("3. 質問アシスタント")
st.caption("例：あと何単位履修できますか？ / 卒業できますか？ / 卒業研究を履修できますか？")
with st.form("question_form"):
    question = st.text_input("質問してください", placeholder="大学の規則について質問を入力")
    submitted = st.form_submit_button("規則に基づいて判定", type="primary")

if submitted:
    intent, result = answer_service.answer(question, student)
    st.session_state["last_answer"] = {
        "question": question,
        "intent": intent.value,
        "result": result.model_dump(mode="json"),
    }

if "last_answer" in st.session_state:
    answer = st.session_state["last_answer"]
    result = DecisionResult.model_validate(answer["result"])
    st.markdown("### 回答")
    show_status_answer(result)
    st.caption(f"検出したIntent：`{answer['intent']}`")
    show_decision_basis(result)
    with st.expander("内部判定データ"):
        st.json(
            {
                "question": answer["question"],
                "student_id": student.student_id,
                "intent": answer["intent"],
                "decision_result": result.model_dump(mode="json"),
            }
        )

show_rule_manager(repository)

st.header("5. 研究用情報")
with st.expander("選択中の学生データ"):
    st.json(student.model_dump(mode="json"))
with st.expander("登録ルール一覧"):
    st.json([rule.model_dump(mode="json") for rule in engine.config.rules])
