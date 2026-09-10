"""schema.org を基底とする語彙定義と、CCSO / OLOUD への対応表。

このモジュールは「どの概念を、どの共通語彙のどの用語で表すか」を一箇所に集める。
判定ロジックは持たず、Rule Engine・ナレッジグラフ・JSON-LD出力の三者が
同じ用語定義を参照するための単一の情報源として機能する。

設計方針:

1. schema.org に適切な用語がある概念は、必ず schema.org を使う。
2. schema.org に無く CCSO にある概念（在籍・修得・履修登録など学籍側）は CCSO を使う。
3. どちらにも無い概念だけ、ローカル拡張名前空間 ``urd:`` を定義し、
   近い既存用語があれば ``skos:closeMatch`` として明示する（同一視はしない）。

用語の実在は tests/test_ontology.py が data/schemaorg_terms.json（schema.org
公式配布ファイルから抽出した部分集合）に対して検証する。
"""

from __future__ import annotations

from typing import Any


# --- 名前空間 -------------------------------------------------------------

SCHEMA = "https://schema.org/"
CCSO = "https://w3id.org/ccso/ccso#"
OLOUD = "http://lod.nik.uni-obuda.hu/oloud/oloud#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"

#: 本デモのローカル拡張「語彙」の名前空間。GitHub Pages 版が用語の説明を返す。
URD = "https://brightwaltz.github.io/university-rules-demo/ns#"

#: 本デモの「インスタンス」（実在の学生・科目・規程ノード）の名前空間。
#: 語彙（urd:）とデータ（urdi:）を分けることで、用語かノードかを接頭辞だけで判別できる。
URD_ID = "https://brightwaltz.github.io/university-rules-demo/id/"

#: 語彙の版。schema.org 30.0（2026-03-19 リリース）で用語の実在を確認している。
SCHEMA_ORG_VERSION = "30.0"

#: CCSO の版（ccso.owl の owl:versionInfo）。
CCSO_VERSION = "0.7"

JSONLD_CONTEXT: dict[str, str] = {
    "schema": SCHEMA,
    "ccso": CCSO,
    "oloud": OLOUD,
    "skos": SKOS,
    "rdfs": RDFS,
    "xsd": XSD,
    "urd": URD,
    "urdi": URD_ID,
}


def expand(curie: str) -> str:
    """``schema:name`` のようなCURIEを絶対IRIへ展開する。"""
    prefix, _, local = curie.partition(":")
    namespace = JSONLD_CONTEXT.get(prefix)
    if not local or namespace is None:
        raise ValueError(f"未知の名前空間です: {curie}")
    return f"{namespace}{local}"


# --- 学生（学籍データ）側の用語 -------------------------------------------
#
# schema.org は科目カタログ・規程・通知の語彙は十分に持つが、
# 「誰が何を修得したか」という学籍側の語彙をほとんど持たない。
# schema:numberOfCredits の domain も Course と EducationalOccupationalProgram
# だけで、Person には付けられない。そこを CCSO が埋める。

STUDENT_TERMS: dict[str, str] = {
    "student_id": "schema:identifier",
    "name": "schema:name",
    "faculty": "schema:affiliation",
    "year_of_study": "urd:yearOfStudy",
    "earned_credits": "urd:earnedCredits",
    "current_registered_credits": "urd:registeredCredits",
    "completed_courses": "ccso:hasCompleted",
    "registered_courses": "ccso:hasRegistered",
    "registration_completed": "schema:actionStatus",
    "enrolled_program": "ccso:enrolledIn",
}

#: 個別通知ルールの条件で指定できる用語 → (Studentの属性名, 型)
CONDITION_TERMS: dict[str, tuple[str, type]] = {
    "urd:yearOfStudy": ("year_of_study", int),
    "urd:earnedCredits": ("earned_credits", int),
    "urd:registeredCredits": ("current_registered_credits", int),
    "schema:actionStatus": ("registration_completed", bool),
    "ccso:hasCompleted": ("completed_courses", list),
    "ccso:hasRegistered": ("registered_courses", list),
}

CONDITION_TERM_LABELS: dict[str, str] = {
    "urd:yearOfStudy": "学年",
    "urd:earnedCredits": "修得単位数",
    "urd:registeredCredits": "履修登録単位数",
    "schema:actionStatus": "履修登録の完了状態",
    "ccso:hasCompleted": "修得済み科目",
    "ccso:hasRegistered": "履修登録済み科目",
}


# --- ルール種別ごとのオントロジー束縛 ---------------------------------------


class RuleOntology:
    """ルール種別ひとつ分の語彙束縛。

    Attributes:
        label: 画面表示名。
        subject_class: そのルールが制約する主語のクラス（CURIE）。
        rule_terms: ルール側の設定値が対応する用語（YAMLキー → CURIE）。
        evaluated_terms: 判定時に学生・グラフから読む用語（CURIE）。
        horn_clause: 判定を Horn節 として書き下したもの。処理系には渡さず、
            「どの原子論理式を根拠にしたか」を人間とレビュアーへ示すために使う。
    """

    def __init__(
        self,
        label: str,
        subject_class: str,
        rule_terms: dict[str, str],
        evaluated_terms: list[str],
        horn_clause: str,
    ):
        self.label = label
        self.subject_class = subject_class
        self.rule_terms = rule_terms
        self.evaluated_terms = evaluated_terms
        self.horn_clause = horn_clause

    def terms(self) -> list[str]:
        """このルールが触れる全用語（重複排除・出現順）。"""
        ordered = [self.subject_class, *self.rule_terms.values(), *self.evaluated_terms]
        seen: dict[str, None] = {}
        for term in ordered:
            seen.setdefault(term, None)
        return list(seen)


RULE_TYPE_ONTOLOGY: dict[str, RuleOntology] = {
    "registration_deadline": RuleOntology(
        label="履修登録期限",
        subject_class="schema:EducationalOccupationalProgram",
        # schema.org に完全に一致する用語がある。
        rule_terms={"deadline": "schema:applicationDeadline"},
        evaluated_terms=["urd:evaluationDate"],
        horn_clause=(
            "registration_deadline(Program, Deadline, DaysLeft) :- "
            "schema:applicationDeadline(Program, Deadline), "
            "urd:evaluationDate(Today), DaysLeft is Deadline - Today."
        ),
    ),
    "annual_credit_limit": RuleOntology(
        label="年間履修上限",
        subject_class="schema:EducationalOccupationalProgram",
        # 「年間の履修上限」に相当する用語は schema.org にも CCSO にも無い。
        # schema:numberOfCredits は課程全体の単位数であって年間上限ではない。
        rule_terms={"max_credits": "urd:annualCreditLimit"},
        evaluated_terms=["ccso:hasRegistered", "schema:numberOfCredits"],
        horn_clause=(
            "remaining_credits(Student, Remaining) :- "
            "ccso:enrolledIn(Student, Program), "
            "urd:annualCreditLimit(Program, Limit), "
            "urd:registeredCredits(Student, Registered), "
            "Remaining is max(0, Limit - Registered)."
        ),
    ),
    "graduation_credit_requirement": RuleOntology(
        label="卒業必要単位数",
        subject_class="schema:EducationalOccupationalProgram",
        # 課程の修了に必要な総単位数は schema:numberOfCredits そのもの。
        rule_terms={"required_credits": "schema:numberOfCredits"},
        evaluated_terms=["urd:earnedCredits"],
        horn_clause=(
            "meets_credit_requirement(Student) :- "
            "ccso:enrolledIn(Student, Program), "
            "schema:numberOfCredits(Program, Required), "
            "urd:earnedCredits(Student, Earned), Earned >= Required."
        ),
    ),
    "graduation_required_courses": RuleOntology(
        label="卒業必修科目",
        subject_class="schema:EducationalOccupationalProgram",
        # schema:programPrerequisites は「課程に入るための前提」であって
        # 「修了に必要な科目」ではないため、意味が違う。urd: を定義し
        # closeMatch に留める（CROSSWALK 参照）。
        rule_terms={"required_courses": "urd:requiredCourse"},
        evaluated_terms=["ccso:hasCompleted"],
        horn_clause=(
            "meets_course_requirement(Student) :- "
            "ccso:enrolledIn(Student, Program), "
            "forall(urd:requiredCourse(Program, Course), "
            "ccso:hasCompleted(Student, Course))."
        ),
    ),
    "thesis_eligibility": RuleOntology(
        label="卒業研究履修条件",
        subject_class="schema:Course",
        rule_terms={
            "required_courses": "schema:coursePrerequisites",
            "minimum_year_of_study": "urd:minimumYearOfStudy",
            "minimum_earned_credits": "urd:minimumEarnedCredits",
        },
        evaluated_terms=["urd:yearOfStudy", "urd:earnedCredits", "ccso:hasCompleted"],
        horn_clause=(
            "eligible_to_register(Student, Thesis) :- "
            "urd:minimumYearOfStudy(Thesis, MinYear), "
            "urd:yearOfStudy(Student, Year), Year >= MinYear, "
            "urd:minimumEarnedCredits(Thesis, MinCredits), "
            "urd:earnedCredits(Student, Earned), Earned >= MinCredits, "
            "forall(schema:coursePrerequisites(Thesis, Course), "
            "ccso:hasCompleted(Student, Course))."
        ),
    ),
    "personalized_notification": RuleOntology(
        label="個別通知ルール",
        subject_class="schema:Person",
        rule_terms={"message": "schema:text", "level": "urd:notificationLevel"},
        evaluated_terms=[],
        horn_clause=(
            "notify(Student, Message) :- "
            "condition_1(Student), ..., condition_n(Student), "
            "schema:text(Rule, Message)."
        ),
    ),
}

RULE_TYPE_LABELS: dict[str, str] = {
    rule_type: ontology.label for rule_type, ontology in RULE_TYPE_ONTOLOGY.items()
}

SINGLETON_RULE_TYPES = set(RULE_TYPE_LABELS) - {"personalized_notification"}


# --- 語彙対応表 -------------------------------------------------------------
#
# 報告時に「なぜこの用語を選んだか」を説明するための表。
# CCSO は自身が schema:Organization / schema:EducationalOrganization を
# 直接再利用しており（ccso.owl で確認）、schema.org と併用できる。
# OLOUD は AIISO / FOAF / Dublin Core の上に構築されており schema.org は使わない。
# OLOUD 側の用語は原論文（Acta Polytechnica Hungarica 14(4), 2017）に
# 明記されているものだけを載せている。

CROSSWALK: list[dict[str, str]] = [
    {
        "concept": "大学",
        "demo": "schema:CollegeOrUniversity",
        "schema_org": "schema:CollegeOrUniversity",
        "ccso": "ccso:University",
        "oloud": "（aiiso 経由）",
    },
    {
        "concept": "学部",
        "demo": "schema:EducationalOrganization",
        "schema_org": "schema:EducationalOrganization",
        "ccso": "ccso:School",
        "oloud": "aiiso:Department",
    },
    {
        "concept": "課程・プログラム",
        "demo": "schema:EducationalOccupationalProgram",
        "schema_org": "schema:EducationalOccupationalProgram",
        "ccso": "ccso:ProgramofStudy",
        "oloud": "oloud:studyProgramme",
    },
    {
        "concept": "科目",
        "demo": "schema:Course",
        "schema_org": "schema:Course",
        "ccso": "ccso:Course",
        "oloud": "aiiso:Subject / aiiso:Course",
    },
    {
        "concept": "単位数",
        "demo": "schema:numberOfCredits",
        "schema_org": "schema:numberOfCredits",
        "ccso": "ccso:creditsECTS",
        "oloud": "oloud:subjectCredit",
    },
    {
        "concept": "科目コード",
        "demo": "schema:courseCode",
        "schema_org": "schema:courseCode",
        "ccso": "ccso:code",
        "oloud": "aiiso:code",
    },
    {
        "concept": "学生",
        "demo": "schema:Person + ccso:UndergraduateStudent",
        "schema_org": "schema:Person",
        "ccso": "ccso:Student / ccso:UndergraduateStudent",
        "oloud": "foaf:Person",
    },
    {
        "concept": "在籍",
        "demo": "ccso:enrolledIn",
        "schema_org": "（該当なし）",
        "ccso": "ccso:enrolledIn",
        "oloud": "（該当なし）",
    },
    {
        "concept": "修得済み科目",
        "demo": "ccso:hasCompleted",
        "schema_org": "（該当なし）",
        "ccso": "ccso:hasCompleted",
        "oloud": "（該当なし）",
    },
    {
        "concept": "履修登録済み科目",
        "demo": "ccso:hasRegistered",
        "schema_org": "（該当なし）",
        "ccso": "ccso:hasRegistered",
        "oloud": "（該当なし）",
    },
    {
        "concept": "履修登録という行為と其の状態",
        "demo": "schema:RegisterAction + schema:actionStatus",
        "schema_org": "schema:RegisterAction / schema:actionStatus",
        "ccso": "（該当なし）",
        "oloud": "（該当なし）",
    },
    {
        "concept": "履修登録期限",
        "demo": "schema:applicationDeadline",
        "schema_org": "schema:applicationDeadline",
        "ccso": "（該当なし）",
        "oloud": "（該当なし）",
    },
    {
        "concept": "学位",
        "demo": "schema:EducationalOccupationalCredential",
        "schema_org": "schema:EducationalOccupationalCredential",
        "ccso": "ccso:Bachelor / ccso:hasDegree",
        "oloud": "oloud:degree",
    },
    {
        "concept": "学則・履修規程",
        "demo": "schema:Legislation",
        "schema_org": "schema:Legislation",
        "ccso": "（該当なし）",
        "oloud": "（該当なし）",
    },
    {
        "concept": "通知",
        "demo": "schema:Message",
        "schema_org": "schema:Message",
        "ccso": "（該当なし）",
        "oloud": "（該当なし）",
    },
    {
        "concept": "学年",
        "demo": "urd:yearOfStudy",
        "schema_org": "（該当なし）",
        "ccso": "（該当なし）",
        "oloud": "（該当なし）",
    },
    {
        "concept": "修得単位数の合計",
        "demo": "urd:earnedCredits",
        "schema_org": "（該当なし。numberOfCredits は Person に付けられない）",
        "ccso": "（該当なし。ccso:hasCompleted から集計する）",
        "oloud": "（該当なし）",
    },
    {
        "concept": "年間履修上限",
        "demo": "urd:annualCreditLimit",
        "schema_org": "（該当なし）",
        "ccso": "（該当なし）",
        "oloud": "（該当なし）",
    },
    {
        "concept": "卒業必修科目",
        "demo": "urd:requiredCourse",
        "schema_org": "schema:programPrerequisites（closeMatch。入学要件の意味なので同一視しない）",
        "ccso": "ccso:hasPrerequisite（closeMatch）",
        "oloud": "oloud:subjectRequires（closeMatch）",
    },
]

#: ローカル拡張用語の定義。JSON-LD へ rdfs:label / skos:closeMatch として出力する。
LOCAL_TERM_DEFINITIONS: dict[str, dict[str, Any]] = {
    "urd:yearOfStudy": {
        "label": "学年",
        "comment": "学生が在籍している年次。schema.org・CCSO ともに該当用語が無い。",
        "close_match": [],
    },
    "urd:earnedCredits": {
        "label": "修得単位数",
        "comment": (
            "ccso:hasCompleted で結ばれた科目の schema:numberOfCredits の合計。"
            "schema:numberOfCredits は Person を domain に取れないため別用語とした。"
        ),
        "close_match": [],
    },
    "urd:registeredCredits": {
        "label": "履修登録単位数",
        "comment": "ccso:hasRegistered で結ばれた科目の schema:numberOfCredits の合計。",
        "close_match": [],
    },
    "urd:annualCreditLimit": {
        "label": "年間履修上限単位数",
        "comment": "1年度に履修登録できる単位数の上限。schema.org に該当用語が無い。",
        "close_match": [],
    },
    "urd:requiredCourse": {
        "label": "卒業必修科目",
        "comment": (
            "課程を修了するために修得が必要な科目。"
            "schema:programPrerequisites は入学要件を指すため同一視しない。"
        ),
        "close_match": ["schema:programPrerequisites", "ccso:hasPrerequisite"],
    },
    "urd:minimumYearOfStudy": {
        "label": "履修に必要な最低学年",
        "comment": "科目を履修登録するために必要な最低年次。",
        "close_match": [],
    },
    "urd:minimumEarnedCredits": {
        "label": "履修に必要な最低修得単位数",
        "comment": "科目を履修登録するために必要な修得単位数の下限。",
        "close_match": ["schema:coursePrerequisites"],
    },
    "urd:evaluationDate": {
        "label": "判定基準日",
        "comment": "デモを再現可能にするために固定した評価日。",
        "close_match": [],
    },
    "urd:notificationLevel": {
        "label": "通知レベル",
        "comment": "通知の重要度。info / success / warning / error。",
        "close_match": [],
    },
    "urd:courseCategory": {
        "label": "科目区分",
        "comment": "教養科目・専門基礎科目などの区分。schema:DefinedTerm を値に取る。",
        "close_match": ["schema:about"],
    },
    "urd:semester": {
        "label": "学期",
        "comment": "前期（first）／後期（second）。",
        "close_match": ["oloud:courseTerm"],
    },
    # --- 判定結果まわり。schema.org は「規則に基づく判定とその根拠」を
    #     表す語彙を持たないため、ここだけはローカルに定義する。
    "urd:Rule": {
        "label": "ルール",
        "comment": "data/rules.yaml の1件。規程の条文を機械可読にしたもの。",
        "close_match": [],
    },
    "urd:DecisionResult": {
        "label": "判定結果",
        "comment": "Rule Engine が返した決定論的な判定。生成AIは介在しない。",
        "close_match": [],
    },
    "urd:Premise": {
        "label": "前提",
        "comment": "判定の根拠となった原子論理式ひとつ分。Horn節の本体に対応する。",
        "close_match": [],
    },
    "urd:constrains": {
        "label": "制約対象",
        "comment": "ルールが制約するノード。",
        "close_match": [],
    },
    "urd:basedOnLegislation": {
        "label": "根拠条文",
        "comment": "ルールの根拠となる schema:Legislation ノード。",
        "close_match": ["schema:citation"],
    },
    "urd:hornClause": {
        "label": "Horn節",
        "comment": "判定をHorn節として書き下したもの。人間のレビュー用で、実行はしない。",
        "close_match": [],
    },
    "urd:status": {
        "label": "判定区分",
        "comment": "eligible / not_eligible / info / unknown。",
        "close_match": [],
    },
    "urd:appliedRule": {
        "label": "適用ルール",
        "comment": "判定に使ったルール。",
        "close_match": [],
    },
    "urd:usesTerm": {
        "label": "使用用語",
        "comment": "判定が読んだオントロジー用語。",
        "close_match": [],
    },
    "urd:premise": {
        "label": "前提",
        "comment": "判定結果が持つ前提の並び。",
        "close_match": [],
    },
    "urd:satisfied": {
        "label": "充足",
        "comment": "その前提が満たされたかどうか。",
        "close_match": [],
    },
    "urd:comparator": {
        "label": "比較演算子",
        "comment": ">= や contains など、前提の評価に使った演算子。",
        "close_match": [],
    },
    "urd:actualValue": {
        "label": "実際の値",
        "comment": "グラフから読み取った値。",
        "close_match": [],
    },
    "urd:expectedValue": {
        "label": "期待する値",
        "comment": "ルールが要求する値。",
        "close_match": [],
    },
    "urd:unmetCondition": {
        "label": "満たしていない条件",
        "comment": "不足している要件の説明文。",
        "close_match": [],
    },
    "urd:calculation": {
        "label": "計算式",
        "comment": "数値判定の計算過程。",
        "close_match": [],
    },
    "urd:decision": {
        "label": "判定",
        "comment": "通知の根拠となった判定結果。",
        "close_match": [],
    },
    "urd:question": {
        "label": "質問",
        "comment": "利用者が入力した自然言語の質問。",
        "close_match": [],
    },
    "urd:detectedIntent": {
        "label": "検出したIntent",
        "comment": "ルールベースIntent Detectionが返した意図。",
        "close_match": [],
    },
}


def all_curies() -> list[str]:
    """デモが使用する全CURIE（テストで実在検証する対象）。"""
    curies: dict[str, None] = {}
    for term in STUDENT_TERMS.values():
        curies.setdefault(term, None)
    for term in CONDITION_TERMS:
        curies.setdefault(term, None)
    for ontology in RULE_TYPE_ONTOLOGY.values():
        for term in ontology.terms():
            curies.setdefault(term, None)
    for term in LOCAL_TERM_DEFINITIONS:
        curies.setdefault(term, None)
    return list(curies)
