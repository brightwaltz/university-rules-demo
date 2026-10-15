from src.horn_reading import annotate, read_clause, read_predicate, read_rule_type
from src.ontology import HORN_PROGRAM


def test_reading_of_the_thesis_entry_clause():
    clause = HORN_PROGRAM.clauses_for("eligible_to_register/2")[0]
    assert read_clause(clause) == {
        "head": "〈学生〉が〈卒業研究〉を履修できる",
        "conditions": [
            "〈学生〉の学年が、〈卒業研究〉の履修に必要な学年以上である",
            "〈学生〉の修得単位数が、〈卒業研究〉の履修に必要な単位数以上である",
            "〈学生〉が、〈卒業研究〉の前提科目をすべて修得している",
        ],
    }


def test_general_form_names_variables_without_repeating_the_head():
    clause = HORN_PROGRAM.clauses_for("meets_first_year_requirement/1")[0]
    assert read_clause(clause) == {
        "head": "〈学生〉が、1年次の修得単位要件を満たしている",
        "conditions": [
            "〈学生〉が1年次に修得した単位数は〈修得単位数〉",
            "〈学生〉が1年次に修得すべき単位数は〈必要単位数〉",
            "〈修得単位数〉が〈必要単位数〉以上",
        ],
    }
    completed = read_clause(HORN_PROGRAM.clauses_for("completed_in_year/3")[0])
    assert completed["head"] == "〈学生〉が〈科目〉を修得したときの学年は〈対象年次〉"


def test_term_readings_and_rdf_type_use_labels():
    completed = read_clause(HORN_PROGRAM.clauses_for("completed_in_year/3")[0])
    assert completed["conditions"] == [
        "〈修得の記録〉を行ったのは〈学生〉",
        "〈修得の記録〉は達成行為（単位修得の記録）である",
        "〈修得の記録〉の時点の学年は〈対象年次〉",
        "〈修得の記録〉の対象は〈科目〉",
    ]
    credits = read_clause(HORN_PROGRAM.clauses_for("first_year_credits/2")[0])
    assert credits["conditions"][0] == "〈学生〉は〈課程〉に在籍している"
    prerequisites = read_clause(HORN_PROGRAM.clauses_for("thesis_prerequisites_ok/2")[0])
    assert prerequisites["conditions"][:2] == ["〈学生〉は人である", "〈卒業研究〉は科目である"]


def test_reading_of_builtins_and_special_forms():
    year = read_clause(HORN_PROGRAM.clauses_for("thesis_year_ok/2")[0])
    assert year["conditions"][-1] == "〈学年〉が〈必要な学年〉以上"
    minimum = read_clause(HORN_PROGRAM.clauses_for("first_year_minimum/2")[0])
    assert minimum["conditions"][-1] == (
        "〈必要単位数〉は ceiling(〈卒業に必要な単位数〉 * 〈割合（%）〉 / 100) で求める"
    )
    credits = read_clause(HORN_PROGRAM.clauses_for("first_year_credits/2")[0])
    assert credits["conditions"][-1] == (
        "〈学生〉が〈科目〉を修得したときの学年は〈対象年次〉、かつ〈科目〉の単位数は〈単位数〉、"
        "であるような〈単位数〉の合計は〈修得単位数〉"
    )
    courses = read_clause(HORN_PROGRAM.clauses_for("meets_course_requirement/1")[0])
    # 条件の読みが「である」で終わるので「、である」を重ねない
    assert courses["conditions"][-1] == (
        "〈必修科目〉は〈課程〉の卒業必修科目であるものすべてについて、"
        "〈学生〉は〈必修科目〉を修得している"
    )


def test_term_definition_clause_uses_its_own_variable_names():
    [earned] = read_predicate("urd:earnedCredits/2")
    assert earned["head"] == "〈学生〉の修得単位数は〈修得単位数〉"
    assert earned["conditions"] == [
        "〈学生〉は人である",
        "〈学生〉は〈科目〉を修得している、かつ〈科目〉の単位数は〈単位数〉、"
        "であるような〈単位数〉の合計は〈修得単位数〉",
    ]


def test_read_rule_type_lists_its_own_clauses_only():
    readings = read_rule_type("graduation_credit_requirement")
    assert [r["head"] for r in readings] == [
        "〈学生〉が卒業に必要な単位数を修得している",
        "〈学生〉が卒業要件を満たしている",
    ]
    assert readings[0]["conditions"][1] == "〈課程〉の単位数は〈必要な単位数〉"
    assert read_rule_type("registration_deadline") == []


def test_proof_tree_readings_use_names_from_the_graph(engine, graph):
    student = next(s for s in graph.students() if s.student_id == "S001")
    proof = engine.evaluate_thesis_eligibility(student).proof
    assert proof["reading"] == "山田太郎が卒業研究を履修できる"
    year = proof["children"][0]
    assert year["reading"] == "山田太郎の学年が、卒業研究の履修に必要な学年以上である"
    assert [child["reading"] for child in year["children"]][1:] == [
        "山田太郎の学年は3",
        "3が4以上",
    ]


def _walk(proof):
    yield proof
    for child in proof["children"]:
        yield from _walk(child)


def test_proof_tree_of_the_first_year_requirement(engine, graph):
    student = next(s for s in graph.students() if s.student_id == "S002")
    proof = engine.evaluate_first_year_requirement(student).proof
    readings = {node["reading"]: node["proved"] for node in _walk(proof)}
    assert readings["鈴木花子は工学部 学士課程（2026年度）に在籍している"] is True
    assert readings["2023年度（1年次）の単位修得は達成行為（単位修得の記録）である"] is True
    assert readings["28が31以上"] is False
    # 集約の中で未束縛のまま残った変数は〈呼び名〉で示す
    assert readings[
        "鈴木花子が〈科目〉を修得したときの学年は1、かつ〈科目〉の単位数は〈単位数〉、"
        "であるような〈単位数〉の合計は28"
    ] is True


def test_annotate_keeps_the_proof_and_adds_reading():
    proof = {
        "goal": "31 is ceiling(124 * 25 / 100)",
        "kind": "builtin",
        "proved": True,
        "clause": None,
        "detail": None,
        "children": [],
    }
    annotated = annotate(proof)
    assert annotated["reading"] == "31は ceiling(124 * 25 / 100) で求める"
    assert "reading" not in proof
