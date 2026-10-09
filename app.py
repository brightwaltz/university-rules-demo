from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from src.answer_service import AnswerService
from src.examples import (
    EXAMPLE_RULE_LABELS,
    EXAMPLE_RULE_TYPES,
    ExampleRepository,
    ExampleValidationError,
    describe_student,
    evaluate_example,
    is_reviewed,
    matches,
)
from src.horn_reading import proof_goal_key, read_predicate, read_rule_type
from src.intent import RuleBasedIntentDetector
from src.jsonld_export import answer_to_jsonld, effective_graph, notification_to_jsonld
from src.knowledge_graph import KnowledgeGraph
from src.models import DecisionResult, DecisionStatus, Student
from src.notification import NotificationService
from src.ontology import (
    CONDITION_TERM_LABELS,
    CONDITION_TERMS,
    CROSSWALK,
    JSONLD_CONTEXT,
    LOCAL_TERM_DEFINITIONS,
    RULE_TYPE_LABELS,
    RULE_TYPE_ONTOLOGY,
    SCHEMA_ORG_VERSION,
)
from src.rule_engine import RuleEngine
from src.rule_repository import RuleRepository, RuleValidationError


BASE_DIR = Path(__file__).resolve().parent
GRAPH_PATH = BASE_DIR / "data" / "university_graph.jsonld"
RULES_PATH = BASE_DIR / "data" / "rules.yaml"
EXAMPLES_PATH = BASE_DIR / "data" / "examples.yaml"

EXAMPLE_STATUS_LABELS = {"eligible": "満たす", "not_eligible": "満たさない", "unknown": "判定すべきでない"}
EXAMPLE_VERDICT_LABELS = {"unreviewed": "未確認", "agree": "正しい", "disagree": "正しくない", "unsure": "わからない"}
EXAMPLE_ORIGIN_LABELS = {"ai": "AI", "boundary": "境界値", "human": "担当者"}
NO_STATUS_LABEL = "―"


def build_services() -> tuple[KnowledgeGraph, RuleEngine, AnswerService, NotificationService]:
    graph = KnowledgeGraph(GRAPH_PATH)
    engine = RuleEngine(RULES_PATH, graph)
    answer_service = AnswerService(engine, RuleBasedIntentDetector())
    return graph, engine, answer_service, NotificationService(engine)


def term_link(curie: str) -> str:
    """CURIEを、語彙の定義ページへのリンク付きで表示する。"""
    prefix, _, local = curie.partition(":")
    namespace = JSONLD_CONTEXT.get(prefix)
    if prefix in {"schema", "ccso"} and namespace:
        return f"[`{curie}`]({namespace}{local})"
    return f"`{curie}`"


def show_ontology_terms(result: DecisionResult) -> None:
    if not result.ontology_terms:
        return
    st.markdown("**使用したオントロジー用語**")
    st.markdown("　" + " / ".join(term_link(term) for term in result.ontology_terms))


def show_premises(result: DecisionResult) -> None:
    if not result.premises:
        return
    st.markdown("**判定に使った原子論理式**")
    rows = []
    for premise in result.premises:
        expected = "" if premise.expected is None else str(premise.expected)
        comparator = premise.comparator or ""
        rows.append(
            {
                "": "✅" if premise.satisfied else "❌",
                "用語": premise.term,
                "内容": premise.label,
                "実際の値": str(premise.actual),
                "比較": comparator,
                "要求値": expected,
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)


def show_clause_readings(readings: list[dict[str, Any]]) -> None:
    """節の読み下し（「{head} のは、次のすべてが成り立つときです」＋番号つき条件）。"""
    for reading in readings:
        lines = [f"{reading['head']} のは、次のすべてが成り立つときです。"]
        lines.extend(
            f"{number}. {condition}"
            for number, condition in enumerate(reading["conditions"], start=1)
        )
        st.markdown(lines[0] + "\n\n" + "\n".join(lines[1:]))


def _proof_lines(node: dict[str, Any], depth: int, lines: list[str]) -> None:
    mark = "✅" if node["proved"] else "❌"
    detail = f"（{html.escape(str(node['detail']))}）" if node.get("detail") else ""
    lines.append(
        f'<div style="margin-left: {depth * 1.4}em">{mark} {html.escape(str(node.get("reading") or node["goal"]))}'
        f"{detail} <small><code>{html.escape(str(node['goal']))}</code></small></div>"
    )
    for child in node["children"]:
        _proof_lines(child, depth + 1, lines)


def show_proof(proof: dict[str, Any]) -> None:
    """証明木を ✅/❌ つきの字下げで示す。各行は読み下しを主に、形式表記を小さく併記する。"""
    lines: list[str] = []
    _proof_lines(proof, 0, lines)
    # 通知の expander の中でも使うので、st.expander ではなく HTML の details で畳む
    st.markdown(
        "<details><summary><strong>証明木</strong>（処理系が実際にたどった導出）</summary>"
        + "".join(lines)
        + "</details>",
        unsafe_allow_html=True,
    )


def show_decision_basis(result: DecisionResult) -> None:
    st.markdown("#### 判定根拠")
    if not result.rule_references:
        st.caption("適用できる登録済みルールはありません。")
        return

    for reference in result.rule_references:
        st.markdown(f"- **{reference.rule_id}** — {reference.title}")
        citation = reference.legislation_identifier or reference.source
        st.markdown(f"  - 出典：{citation}")
        if reference.legislation_iri:
            st.markdown(f"  - 根拠条文ノード：`{reference.legislation_iri}`")

    if result.horn_clause:
        st.markdown("**このルールのHorn節**")
        st.code(result.horn_clause, language="prolog")
    if result.proof:
        key = proof_goal_key(result.proof)
        if key:
            st.markdown("**読み下し**")
            show_clause_readings(read_predicate(key, graph))
        show_proof(result.proof)
    show_premises(result)
    show_ontology_terms(result)

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
    prefix: str, rule_type: str, defaults: dict[str, Any], graph: KnowledgeGraph
) -> dict[str, Any]:
    ontology = RULE_TYPE_ONTOLOGY[rule_type]
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
            "根拠・出典（表示用）", value=str(defaults.get("source", "")), key=f"{prefix}_source"
        ),
    }
    st.caption(f"このルール種別が制約するクラス：`{ontology.subject_class}`")

    if rule_type != "personalized_notification":
        subject_options = _subject_options(graph, ontology.subject_class)
        current_subject = str(defaults.get("subject", ""))
        payload["subject"] = st.selectbox(
            "対象ノード（ナレッジグラフ）",
            options=subject_options,
            index=subject_options.index(current_subject) if current_subject in subject_options else 0,
            format_func=lambda iri: f"{graph.label_of(iri)}（{iri}）",
            key=f"{prefix}_subject",
        )
        articles = graph.legislation_articles()
        article_iris = [article.iri for article in articles]
        article_labels = {article.iri: article for article in articles}
        current_article = str(defaults.get("legislation", ""))
        payload["legislation"] = st.selectbox(
            "根拠条文（schema:Legislation）",
            options=article_iris,
            index=article_iris.index(current_article) if current_article in article_iris else 0,
            format_func=lambda iri: f"{article_labels[iri].citation}　{article_labels[iri].name}",
            key=f"{prefix}_legislation",
        )

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
        payload["required_courses"] = _course_multiselect(
            "卒業必修科目", defaults, graph, f"{prefix}_grad_courses"
        )
    elif rule_type == "thesis_eligibility":
        payload["minimum_year_of_study"] = st.number_input(
            "最低学年",
            min_value=1,
            value=int(defaults.get("minimum_year_of_study", 4)),
            key=f"{prefix}_minimum_year",
        )
        payload["minimum_earned_credits"] = st.number_input(
            "最低修得単位数",
            min_value=0,
            value=int(defaults.get("minimum_earned_credits", 100)),
            key=f"{prefix}_minimum_credits",
        )
        payload["required_courses"] = _course_multiselect(
            "前提科目", defaults, graph, f"{prefix}_thesis_courses"
        )
    elif rule_type == "first_year_credit_requirement":
        payload["year_of_study"] = st.number_input(
            "対象年次",
            min_value=1,
            max_value=6,
            value=int(defaults.get("year_of_study", 1)),
            key=f"{prefix}_target_year",
        )
        payload["minimum_ratio_percent"] = st.number_input(
            "卒業必要単位数に対する最低修得割合（%）",
            min_value=1,
            max_value=100,
            value=int(defaults.get("minimum_ratio_percent", 25)),
            key=f"{prefix}_minimum_ratio",
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
        st.caption(
            "条件はオントロジー用語で指定します。すべての条件を満たした学生にだけ通知します。"
            "科目の用語（ccso:hasCompleted / ccso:hasRegistered）には contains / not_contains を、"
            "数値には比較演算子を使います。"
        )
        raw_conditions = defaults.get(
            "conditions", [{"term": "urd:yearOfStudy", "operator": ">=", "value": 4}]
        )
        default_conditions = [
            {
                "term": condition.get("term", "urd:yearOfStudy"),
                "operator": condition.get("operator", ">="),
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
                "term": st.column_config.SelectboxColumn(
                    "用語",
                    options=list(CONDITION_TERMS),
                    required=True,
                ),
                "operator": st.column_config.SelectboxColumn(
                    "比較",
                    options=["==", "!=", ">=", "<=", ">", "<", "contains", "not_contains"],
                    required=True,
                ),
                "value": st.column_config.TextColumn(
                    "条件値",
                    help="数値、true / false、または科目コード（例：REQ-B）",
                    required=True,
                ),
            },
            key=f"{prefix}_conditions",
        )
        payload["conditions"] = _records_from_editor(edited_conditions)
        with st.expander("指定できる用語"):
            for term, label in CONDITION_TERM_LABELS.items():
                st.markdown(f"- {term_link(term)} — {label}")
    return payload


def _subject_options(graph: KnowledgeGraph, subject_class: str) -> list[str]:
    return [
        str(node["@id"])
        for node in graph.nodes.values()
        if subject_class in graph.types_of(node)
    ]


def _course_multiselect(
    label: str, defaults: dict[str, Any], graph: KnowledgeGraph, key: str
) -> list[str]:
    codes = [course.code for course in graph.courses()]
    labels = {course.code: f"{course.name}（{course.code} / {course.credits}単位）" for course in graph.courses()}
    selected = [code for code in defaults.get("required_courses", []) if code in codes]
    return st.multiselect(
        label,
        options=codes,
        default=selected,
        format_func=lambda code: labels.get(code, code),
        key=key,
    )


def show_rule_manager(repository: RuleRepository, graph: KnowledgeGraph) -> None:
    st.header("4. ルール管理")
    st.caption(
        "変更は data/rules.yaml に保存され、直後の回答・通知から反映されます。"
        "対象ノード・根拠条文・必修科目は、ナレッジグラフに存在するものだけを選べます。"
    )

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
                "対象ノード": str(getattr(rule, "subject", "") or "—"),
                "根拠条文": (
                    graph.legislation(getattr(rule, "legislation", None)).citation
                    if graph.legislation(getattr(rule, "legislation", None))
                    else rule.source
                ),
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
            payload = rule_form_fields("add", add_type, defaults, graph)
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
                f"edit_{selected_rule_id}", selected_rule.rule_type, selected_defaults, graph
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


def show_ontology_section(graph: KnowledgeGraph, repository: RuleRepository) -> None:
    st.header("5. オントロジーとナレッジグラフ")
    st.caption(
        f"schema.org {SCHEMA_ORG_VERSION} を基底に、学籍側の語彙を CCSO で補い、"
        "どちらにも無い概念だけをローカル拡張 urd: として定義しています。"
    )

    with st.expander("語彙対応表（schema.org / CCSO / OLOUD）"):
        st.dataframe(
            [
                {
                    "概念": row["concept"],
                    "本デモ": row["demo"],
                    "schema.org": row["schema_org"],
                    "CCSO": row["ccso"],
                    "OLOUD": row["oloud"],
                }
                for row in CROSSWALK
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "CCSO は schema:Organization / schema:EducationalOrganization を直接再利用しており、"
            "schema.org と併用できます。OLOUD は AIISO / FOAF / Dublin Core の上に構築されており、"
            "schema.org は使っていません。どちらも schema.org の一部ではありません。"
        )

    with st.expander("ローカル拡張用語（urd:）の定義"):
        st.dataframe(
            [
                {
                    "用語": curie,
                    "名称": definition["label"],
                    "説明": definition["comment"],
                    "近い既存用語": "、".join(definition["close_match"]) or "—",
                }
                for curie, definition in LOCAL_TERM_DEFINITIONS.items()
            ],
            width="stretch",
            hide_index=True,
        )

    with st.expander("ルール種別ごとの「規程の本文 → Horn節 → 読み下し」"):
        rules = repository.list_rules()
        for rule_type, ontology in RULE_TYPE_ONTOLOGY.items():
            st.markdown(f"#### {ontology.label}（`{rule_type}`）")
            articles = [
                graph.legislation(getattr(rule, "legislation", None))
                for rule in rules
                if rule.rule_type == rule_type
            ]
            texts = [article for article in articles if article is not None and article.text]
            st.markdown("**規程の本文（schema:text）**")
            if texts:
                for article in texts:
                    st.markdown(f"> {article.citation}：{article.text}")
            else:
                st.caption("本文を持つ条文に結び付いたルールがありません。")
            st.markdown("**Horn節**")
            st.code(ontology.horn_clause, language="prolog")
            st.markdown("**読み下し**")
            if ontology.executable:
                show_clause_readings(read_rule_type(rule_type, graph))
            else:
                st.caption("この種別の節は表示用で、処理系では実行しません。")

    with st.expander("科目カタログ（schema:Course）"):
        st.dataframe(
            [
                {
                    "科目コード": course.code,
                    "科目名": course.name,
                    "単位数": course.credits,
                    "区分": course.category or "—",
                }
                for course in graph.courses()
            ],
            width="stretch",
            hide_index=True,
        )

    document = effective_graph(graph, repository.list_rules())
    st.download_button(
        "実効グラフをJSON-LDでダウンロード",
        data=json.dumps(document, ensure_ascii=False, indent=2),
        file_name="university_effective_graph.jsonld",
        mime="application/ld+json",
    )
    st.caption(
        "ルールの値は rules.yaml が唯一の情報源です。"
        "書き出すときだけ、対象ノードへ重ねた「実効グラフ」として1つの文書にまとめます。"
    )


@st.cache_data(show_spinner=False)
def evaluate_examples(
    examples_path: str, examples_mtime: float, rules_path: str, rules_mtime: float
) -> list[dict[str, Any]]:
    """例題ごとのシステムの判定。キャッシュのキーは examples.yaml と rules.yaml の更新時刻。"""
    base_graph = KnowledgeGraph(GRAPH_PATH)
    rows = []
    for example in ExampleRepository(examples_path, base_graph).load():
        outcome = evaluate_example(example, rules_path, base_graph)
        rows.append(
            {
                "example": example,
                "description": describe_student(example, base_graph),
                "outcome": outcome.model_dump(mode="json"),
                "matches": matches(example, outcome),
            }
        )
    return rows


def _example_state(row: dict[str, Any]) -> int:
    """並び順：未確認かつ不一致 → 未確認 → 確認済み。"""
    if is_reviewed(row["example"]):
        return 2
    return 1 if row["matches"] else 0


def _example_rule_texts(rule_type: str, repository: RuleRepository, graph: KnowledgeGraph) -> list[str]:
    _, _, text_types = EXAMPLE_RULE_TYPES[rule_type]
    texts = []
    for rule in repository.list_rules():
        if rule.rule_type not in text_types:
            continue
        article = graph.legislation(getattr(rule, "legislation", None))
        if article is not None and article.text:
            texts.append(f"> {article.citation}：{article.text}")
    return texts


def show_example_section(repository: RuleRepository, graph: KnowledgeGraph) -> None:
    st.header("6. 例題で確かめる（業務担当者向け）")
    st.markdown(
        "AI が規程の本文から作った例題と、境界値の例題です。  \n"
        "各例題の『状況』と『結論』を見て、業務として正しいかを選んでください。Horn 節を読む必要はありません。  \n"
        "確認済みの例題は、以後の自動テストでシステムの判定と照合されます。"
    )
    flash = st.session_state.pop("example_flash", None)
    if flash:
        st.success(flash)

    try:
        examples_mtime = EXAMPLES_PATH.stat().st_mtime
        rows = evaluate_examples(
            str(EXAMPLES_PATH), examples_mtime, str(RULES_PATH), RULES_PATH.stat().st_mtime
        )
    except (OSError, ValueError) as exc:
        st.error(f"例題を読み込めませんでした：{exc}")
        return

    reviewed = sum(1 for row in rows if is_reviewed(row["example"]))
    matched = sum(1 for row in rows if row["matches"])
    count_cols = st.columns(5)
    count_cols[0].metric("全件", f"{len(rows)} 件")
    count_cols[1].metric("システムと一致", f"{matched} 件")
    count_cols[2].metric("不一致", f"{len(rows) - matched} 件")
    count_cols[3].metric("確認済み", f"{reviewed} 件")
    count_cols[4].metric("未確認", f"{len(rows) - reviewed} 件")

    filter_cols = st.columns(3)
    rule_filter = filter_cols[0].selectbox(
        "ルール",
        options=["all", *EXAMPLE_RULE_LABELS],
        format_func=lambda key: "すべて" if key == "all" else EXAMPLE_RULE_LABELS[key],
        key="example_filter_rule",
    )
    origin_filter = filter_cols[1].selectbox(
        "出どころ",
        options=["all", *EXAMPLE_ORIGIN_LABELS],
        format_func=lambda key: "すべて" if key == "all" else EXAMPLE_ORIGIN_LABELS[key],
        key="example_filter_origin",
    )
    state_filters = {
        "all": "すべて",
        "unreviewed_mismatch": "未確認かつ不一致",
        "unreviewed": "未確認",
        "reviewed": "確認済み",
        "mismatch": "システムと不一致",
    }
    state_filter = filter_cols[2].selectbox(
        "状態", options=list(state_filters), format_func=state_filters.get, key="example_filter_state"
    )

    def visible(row: dict[str, Any]) -> bool:
        example = row["example"]
        if rule_filter != "all" and example["rule_type"] != rule_filter:
            return False
        if origin_filter != "all" and example["origin"] != origin_filter:
            return False
        return {
            "all": True,
            "unreviewed_mismatch": _example_state(row) == 0,
            "unreviewed": not is_reviewed(example),
            "reviewed": is_reviewed(example),
            "mismatch": not row["matches"],
        }[state_filter]

    shown = sorted((row for row in rows if visible(row)), key=_example_state)
    if not shown:
        st.info("条件に合う例題はありません。")
        return

    table = []
    for row in shown:
        example = row["example"]
        review = example.get("review") or {}
        table.append(
            {
                "例題ID": example["id"],
                "ルール": EXAMPLE_RULE_LABELS[example["rule_type"]],
                "状況": row["description"],
                "AIの予想": EXAMPLE_STATUS_LABELS[example["expected"]["status"]],
                "システムの判定": EXAMPLE_STATUS_LABELS.get(row["outcome"]["status"], row["outcome"]["status"]),
                "一致": "✅" if row["matches"] else "❌",
                "担当者の判断": EXAMPLE_VERDICT_LABELS[review.get("verdict", "unreviewed")],
                "正しい結論": EXAMPLE_STATUS_LABELS.get(review.get("correct_status"), NO_STATUS_LABEL),
                "メモ": review.get("note") or "",
            }
        )
    locked = ["例題ID", "ルール", "状況", "AIの予想", "システムの判定", "一致"]
    edited = st.data_editor(
        table,
        hide_index=True,
        width="stretch",
        disabled=locked,
        column_config={
            "担当者の判断": st.column_config.SelectboxColumn(
                "担当者の判断", options=list(EXAMPLE_VERDICT_LABELS.values()), required=True
            ),
            "正しい結論": st.column_config.SelectboxColumn(
                "正しい結論",
                options=[NO_STATUS_LABEL, *EXAMPLE_STATUS_LABELS.values()],
                required=True,
                help="「正しくない」としたときだけ選びます。",
            ),
            "メモ": st.column_config.TextColumn("メモ"),
        },
        key=f"example_editor_{examples_mtime}_{rule_filter}_{origin_filter}_{state_filter}",
    )

    if st.button("確認結果を保存", type="primary", key="save_example_reviews"):
        verdict_by_label = {label: key for key, label in EXAMPLE_VERDICT_LABELS.items()}
        status_by_label = {label: key for key, label in EXAMPLE_STATUS_LABELS.items()}
        by_id = {row["example"]["id"]: row["example"] for row in shown}
        records = edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
        updates: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for record in records:
            example = by_id[record["例題ID"]]
            review = example.get("review") or {}
            verdict = verdict_by_label.get(record["担当者の判断"], "unreviewed")
            correct = status_by_label.get(record["正しい結論"]) if verdict == "disagree" else None
            note = record.get("メモ")
            note = note.strip() if isinstance(note, str) and note.strip() else None
            if verdict == "disagree" and correct is None:
                missing.append(example["id"])
                continue
            if (verdict, correct, note) == (
                review.get("verdict"),
                review.get("correct_status"),
                review.get("note"),
            ):
                continue
            updates[example["id"]] = {
                **review,
                "verdict": verdict,
                "correct_status": correct,
                "note": note,
                "reviewed_on": None if verdict == "unreviewed" else date.today(),
            }
        if missing:
            st.error(
                "「正しくない」とした例題は、正しい結論を選んでください（保存していません）："
                + "、".join(missing)
            )
        elif not updates:
            st.info("変更はありません。")
        else:
            try:
                ExampleRepository(EXAMPLES_PATH, graph).save_reviews(updates)
                st.session_state["example_flash"] = f"{len(updates)}件の確認結果を保存しました。"
                st.rerun()
            except (ExampleValidationError, OSError) as exc:
                st.error(f"保存できませんでした：{exc}")

    rows_by_id = {row["example"]["id"]: row for row in shown}
    selected_id = st.selectbox(
        "詳しく見る例題",
        options=list(rows_by_id),
        format_func=lambda example_id: f"{example_id}　{rows_by_id[example_id]['example']['title']}",
        key="example_detail",
    )
    row = rows_by_id[selected_id]
    example = row["example"]
    outcome = row["outcome"]
    st.markdown(f"**状況**：{row['description']}")
    st.markdown("**規程の本文（schema:text）**")
    for text in _example_rule_texts(example["rule_type"], repository, graph):
        st.markdown(text)
    origin = EXAMPLE_ORIGIN_LABELS.get(example["origin"], example["origin"])
    st.markdown(
        f"**{origin}の予想**：{EXAMPLE_STATUS_LABELS[example['expected']['status']]}"
        f"（{example['expected'].get('reason') or '理由の記載なし'}）"
    )
    if example.get("ai_note"):
        st.warning(f"AI が気づいた解釈上の疑問：{example['ai_note']}", icon="⚠️")
    st.markdown("**システムの判定**")
    show_status_answer(
        DecisionResult(status=DecisionStatus(outcome["status"]), message=outcome["message"])
    )
    if outcome.get("proof"):
        show_proof(outcome["proof"])


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

graph, engine, answer_service, notification_service = build_services()
repository = RuleRepository(RULES_PATH, graph)
students = graph.students()
student_by_label = {student.display_name: student for student in students}

with st.sidebar:
    st.header("このシステムの仕組み")
    st.markdown(
        """
        <div class="system-flow">
          質問<br>↓<br>Intent Detection<br>↓<br>
          共通オントロジー（schema.org ＋ CCSO）<br>↓<br>
          ナレッジグラフ ＋ Rules as Code<br>↓<br>
          決定論的判定<br>↓<br>回答・通知（JSON-LD）
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "生成AIに大学制度上の判断を直接生成させるのではなく、"
        "共通語彙で書かれたグラフとルールによる決定論的判定を回答に利用しています。"
    )
    st.divider()
    st.write(f"デモ基準日：{engine.evaluation_date:%Y年%m月%d日}")
    st.caption(f"schema.org {SCHEMA_ORG_VERSION} / CCSO 0.7")
    st.caption("APIキー不要 / LLM未使用")

st.title("大学 Rules as Code アシスタント")
st.caption("schema.orgベースのナレッジグラフと大学規則から、根拠に基づいて回答・通知する研究用プロトタイプ")

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
      {student.faculty} {student.year_of_study}年 &nbsp; | &nbsp;
      <code>{student.iri}</code>
    </div>
    """,
    unsafe_allow_html=True,
)
metric_cols = st.columns(4)
metric_cols[0].metric("修得単位", f"{student.earned_credits} 単位")
metric_cols[1].metric("現在履修登録", f"{student.current_registered_credits} 単位")
metric_cols[2].metric("修得済み科目", f"{len(student.completed_courses)} 科目")
metric_cols[3].metric("履修登録", "完了" if student.registration_completed else "未完了")
st.caption(
    "単位数は ccso:hasCompleted / ccso:hasRegistered をたどって "
    "schema:numberOfCredits を合計した導出値です。グラフに数値は書かれていません。"
)
completion_records = graph.completion_records(student.iri) if student.iri else []
if completion_records:
    with st.expander("年次別の修得単位（schema:AchieveAction）"):
        st.dataframe(
            [
                {
                    "年度": record["academic_year"],
                    "年次": f"{record['year_of_study']}年次",
                    "修得単位": record["credits"],
                    "科目数": len(record["course_codes"]),
                }
                for record in completion_records
            ],
            width="stretch",
            hide_index=True,
        )

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
        st.markdown("**schema:Message としての表現**")
        st.json(notification_to_jsonld(notice, student))

st.header("3. 質問アシスタント")
st.caption(
    "例：あと何単位履修できますか？ / 卒業できますか？ / 卒業研究を履修できますか？"
    " / 1年次の単位要件は満たしていますか？"
)
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
    with st.expander("内部判定データ（DecisionResult）"):
        st.json(
            {
                "question": answer["question"],
                "student_id": student.student_id,
                "intent": answer["intent"],
                "decision_result": result.model_dump(mode="json"),
            }
        )
    with st.expander("JSON-LDとしての回答"):
        st.json(answer_to_jsonld(answer["question"], answer["intent"], result, student))

show_rule_manager(repository, graph)
show_ontology_section(graph, repository)
show_example_section(repository, graph)

st.header("7. 研究用情報")
with st.expander("選択中の学生データ（Studentへの射影）"):
    st.json(student.model_dump(mode="json"))
with st.expander("グラフ上の学生ノード（schema:Person）"):
    st.json(graph.node(student.iri) if student.iri else {})
with st.expander("登録ルール一覧"):
    st.json([rule.model_dump(mode="json") for rule in engine.config.rules])
