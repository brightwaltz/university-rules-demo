"""Horn 節インタープリタ（src/horn.py）の単体テスト。

形式表記の文字列は JS 版（docs/assets/horn.js）と一致させる必要があるので、
ここで文字列そのものを固定する。
"""

import pytest

from src.horn import (
    Aggregate,
    Atom,
    BinOp,
    Compare,
    Conj,
    FactBase,
    Forall,
    Func,
    HornError,
    Is,
    Not,
    Program,
    Solver,
    Var,
    atom,
    format_constant,
    format_term,
)


def make_solver(text: str, facts=(), **options) -> Solver:
    program = Program.parse(text, origin="test")
    program.check()
    return Solver(program, FactBase(facts), **options)


def answers(solver: Solver, goal) -> list[dict]:
    return list(solver.solve(goal))


# --- 構文 --------------------------------------------------------------------


def test_parse_curie_predicates_iri_constants_and_comments():
    text = """
    % 先頭のコメント
    req(urdi:course/REQ-A).   % 行末のコメント
    done(S) :- ccso:hasCompleted(S, urdi:course/REQ-A),
               rdf:type(S, schema:Person).
    """
    program = Program.parse(text, origin="rule-type/x")
    first, second = program.clauses
    assert first.head == Atom("req", ("urdi:course/REQ-A",))
    assert first.body == ()
    assert first.source == "req(urdi:course/REQ-A)."
    assert second.head == Atom("done", (Var("S"),))
    assert second.body == (
        Atom("ccso:hasCompleted", (Var("S"), "urdi:course/REQ-A")),
        Atom("rdf:type", (Var("S"), "schema:Person")),
    )
    # source は頭部の先頭から終端の '.' まで（途中の改行・空白はそのまま）
    assert second.source.startswith("done(S) :- ccso:hasCompleted(")
    assert second.source.endswith("rdf:type(S, schema:Person).")
    assert "\n" in second.source
    assert program.defined() == {"req/1", "done/1"}
    assert atom("done", Var("S")).key == "done/1"


def test_parse_strings_numbers_and_zero_arity():
    program = Program.parse(
        "p('it\\'s', \"a\\\\b\", 'x\"y', 12, 3.5).\n"
        "flag.\n"
        "q :- flag, X is 1 + 2 * 3, X >= 7, '2026-04-15' =< '2026-04-30'."
    )
    assert program.clauses[0].head.args == ("it's", "a\\b", 'x"y', 12, 3.5)
    assert program.clauses[1].head == Atom("flag", ())
    body = program.clauses[2].body
    assert body[0] == Atom("flag", ())
    assert body[1] == Is(Var("X"), BinOp("+", 1, BinOp("*", 2, 3)))
    assert body[2] == Compare(">=", Var("X"), 7)
    assert body[3] == Compare("=<", "2026-04-15", "2026-04-30")
    assert make_solver(program.clauses[2].source + "\nflag.").holds(atom("q"))


def test_parse_special_forms():
    program = Program.parse(
        "r(S) :- s(S), \\+ t(S), not((u(S), v(S))),\n"
        "    forall(w(S, C), x(S, C)),\n"
        "    aggregate_all(count, w(S, _), N), aggregate_all(sum(K), (w(S, C2), k(C2, K)), T),\n"
        "    aggregate_all(max(K2), k(_, K2), M), M2 is max(N, T) - ceiling(M / 2), M2 \\= 0."
    )
    body = program.clauses[0].body
    assert body[1] == Not(Atom("t", (Var("S"),)))
    assert body[2] == Not(Conj((Atom("u", (Var("S"),)), Atom("v", (Var("S"),)))))
    assert body[3] == Forall(Atom("w", (Var("S"), Var("C"))), Atom("x", (Var("S"), Var("C"))))
    assert isinstance(body[4], Aggregate) and body[4].kind == "count" and body[4].template is None
    assert body[5].kind == "sum" and isinstance(body[5].goal, Conj)
    assert body[6].kind == "max"
    assert body[7] == Is(
        Var("M2"),
        BinOp("-", Func("max", (Var("N"), Var("T"))), Func("ceiling", (BinOp("/", Var("M"), 2),))),
    )
    assert body[8] == Compare("\\=", Var("M2"), 0)


def test_anonymous_variables_are_distinct():
    program = Program.parse("p :- q(_, _).")
    first, second = program.clauses[0].body[0].args
    assert first != second
    solver = Solver(program, FactBase([("q", ("a", "b"))]))
    assert solver.holds(atom("p"))
    assert format_term(program.clauses[0].body[0]) == "q(_, _)"


@pytest.mark.parametrize(
    ("text", "line", "fragment"),
    [
        ("p(a).\nq(X) :- p(X)\nr.", 3, "'.' が必要です"),
        ("p(a).\n\nq(X) :- X > .", 3, "項が必要です"),
        ("p :- q(X) >= 3.", 1, "アトムの後に比較演算子"),
        ("p :- q ; r.", 1, "解釈できない文字"),
        ("p :- X is foo(1).", 1, "算術式に使えない関数"),
        ("p('abc).", 1, "文字列が閉じていません"),
        ("p('a\\nb').", 1, "使えないエスケープ"),
        ("p :- 3 is X.", 1, "is の左辺は変数"),
        ("X :- p.", 1, "節の頭部は述語"),
        ("p(f(a)).", 1, "複合項"),
    ],
)
def test_syntax_errors_report_origin_and_line(text, line, fragment):
    with pytest.raises(HornError) as error:
        Program.parse(text, origin="rule-type/demo")
    message = str(error.value)
    assert message.startswith(f"rule-type/demo {line}行目: 構文エラー: ")
    assert fragment in message


def test_colon_dash_is_not_part_of_a_name():
    program = Program.parse("p:-q.\nq.")
    assert program.clauses[0].head == Atom("p", ())
    assert program.clauses[0].body == (Atom("q", ()),)


# --- 単一化・選言 ------------------------------------------------------------


def test_unification_and_shared_variables():
    solver = make_solver(
        "same(X) :- pair(X, X).\n"
        "eq(X, Y) :- item(X), item(Y), X = Y.\n"
        "neq(X, Y) :- item(X), item(Y), X \\= Y.",
        [("pair", ("a", "a")), ("pair", ("a", "b")), ("item", ("a",)), ("item", ("b",))],
    )
    assert answers(solver, atom("same", Var("X"))) == [{"X": "a"}]
    assert answers(solver, atom("eq", Var("X"), Var("Y"))) == [{"X": "a", "Y": "a"}, {"X": "b", "Y": "b"}]
    assert answers(solver, atom("neq", "a", Var("Y"))) == [{"Y": "b"}]


def test_constants_unify_by_kind_number_string_bool():
    facts = FactBase([("v", ("n", 2)), ("v", ("s", "2")), ("v", ("b", True)), ("v", ("one", 1))])
    solver = Solver(Program(), facts)
    assert solver.holds(atom("v", "n", 2.0))
    assert not solver.holds(atom("v", "n", "2"))
    assert not solver.holds(atom("v", "s", 2))
    assert solver.holds(atom("v", "b", True))
    # true と 1 は別物（JS と揃える）
    assert not solver.holds(atom("v", "b", 1))
    assert not solver.holds(atom("v", "one", True))


def test_multiple_clauses_are_disjunction_and_facts_come_first():
    solver = make_solver(
        "ok(X) :- a(X).\nok(X) :- b(X).",
        [("a", ("x1",)), ("b", ("x2",)), ("ok", ("x0",))],
    )
    assert [a["X"] for a in solver.solve(atom("ok", Var("X")))] == ["x0", "x1", "x2"]
    assert solver.value(atom("ok", Var("X")), "X") == "x0"
    assert solver.value(atom("ok", "none"), "X") is None


# --- 否定・forall・集約 ------------------------------------------------------


def test_negation_and_not():
    solver = make_solver(
        "free(X) :- item(X), \\+ taken(X).\nfree2(X) :- item(X), not(taken(X)).",
        [("item", ("a",)), ("item", ("b",)), ("taken", ("a",))],
    )
    assert answers(solver, atom("free", Var("X"))) == [{"X": "b"}]
    assert answers(solver, atom("free2", Var("X"))) == [{"X": "b"}]


def test_negation_with_unbound_variable_flounders():
    solver = make_solver("bad(X) :- item(X), \\+ taken(Y).", [("item", ("a",))])
    with pytest.raises(HornError, match="否定の対象に未束縛の変数があります: \\\\\\+ taken\\(Y\\)"):
        solver.holds(atom("bad", Var("X")))


def test_negation_allows_anonymous_variables():
    solver = make_solver(
        "lonely(X) :- item(X), \\+ link(X, _).",
        [("item", ("a",)), ("item", ("b",)), ("link", ("a", "z"))],
    )
    assert answers(solver, atom("lonely", Var("X"))) == [{"X": "b"}]


def test_forall():
    solver = make_solver(
        "all_done(S) :- student(S), forall(required(C), done(S, C)).",
        [
            ("student", ("s1",)),
            ("student", ("s2",)),
            ("required", ("A",)),
            ("required", ("B",)),
            ("done", ("s1", "A")),
            ("done", ("s1", "B")),
            ("done", ("s2", "A")),
        ],
    )
    assert answers(solver, atom("all_done", Var("S"))) == [{"S": "s1"}]
    # 条件に解が無ければ真
    empty = make_solver("ok :- forall(required(C), done(C)).")
    assert empty.holds(atom("ok"))


def test_aggregates_including_empty_sets_and_duplicates():
    facts = [
        ("student", ("s1",)),
        ("student", ("s9",)),
        ("took", ("s1", "c1")),
        ("took", ("s1", "c2")),
        ("took", ("s1", "c3")),
        ("credits", ("c1", 2)),
        ("credits", ("c2", 2)),
        ("credits", ("c3", 4)),
    ]
    solver = make_solver(
        "total(S, T) :- student(S), aggregate_all(sum(K), (took(S, C), credits(C, K)), T).\n"
        "n(S, N) :- student(S), aggregate_all(count, took(S, _), N).\n"
        "top(S, M) :- student(S), aggregate_all(max(K), (took(S, C), credits(C, K)), M).\n"
        "low(S, M) :- student(S), aggregate_all(min(K), (took(S, C), credits(C, K)), M).",
        facts,
    )
    # 同じ値（2単位が2件）も重ねて数える
    assert solver.value(atom("total", "s1", Var("T")), "T") == 8
    assert solver.value(atom("n", "s1", Var("N")), "N") == 3
    assert solver.value(atom("top", "s1", Var("M")), "M") == 4
    assert solver.value(atom("low", "s1", Var("M")), "M") == 2
    # 空集合：sum は 0、count は 0、max/min は失敗
    assert solver.value(atom("total", "s9", Var("T")), "T") == 0
    assert solver.value(atom("n", "s9", Var("N")), "N") == 0
    assert not solver.holds(atom("top", "s9", Var("M")))
    assert not solver.holds(atom("low", "s9", Var("M")))
    # 結果が束縛済みなら比較として働く
    assert solver.holds(atom("total", "s1", 8))
    assert not solver.holds(atom("total", "s1", 7))


# --- 算術・比較 --------------------------------------------------------------


def test_is_normalizes_integral_floats_to_int():
    solver = make_solver(
        "half(X) :- X is 62 / 2.\n"
        "frac(X) :- X is 7 / 2.\n"
        "need(X) :- X is ceiling(124 * 25 / 100).\n"
        "rnd(X) :- X is round(2.5) + floor(1.9) + abs(0 - 3) + min(4, 5) - max(1, 2).",
    )
    half = solver.value(atom("half", Var("X")), "X")
    assert half == 31 and isinstance(half, int)
    assert solver.value(atom("frac", Var("X")), "X") == 3.5
    need = solver.value(atom("need", Var("X")), "X")
    assert need == 31 and isinstance(need, int)
    # round は JS の Math.round と同じく 0.5 を正の方向へ丸める
    assert solver.value(atom("rnd", Var("X")), "X") == 3 + 1 + 3 + 4 - 2


def test_comparisons_numbers_and_iso_dates():
    solver = make_solver(
        "early(D) :- applied(D), deadline(L), D =< L.\nbig(X) :- val(X), X > 3.",
        [
            ("deadline", ("2026-04-15",)),
            ("applied", ("2026-04-01",)),
            ("applied", ("2026-04-16",)),
            ("val", (2,)),
            ("val", (5,)),
        ],
    )
    assert solver.holds(atom("early", "2026-04-01"))
    assert not solver.holds(atom("early", "2026-04-16"))
    assert answers(solver, atom("big", Var("X"))) == [{"X": 5}]


def test_comparison_with_unbound_side_is_an_error():
    solver = make_solver("bad(X) :- val(X), Y > X.", [("val", (1,))])
    with pytest.raises(HornError, match="比較の両辺が束縛されていません: Y > 1"):
        solver.holds(atom("bad", Var("X")))
    anonymous = make_solver("bad(X) :- val(X), X > _.", [("val", (1,))])
    with pytest.raises(HornError, match="比較の両辺が束縛されていません: 1 > _"):
        anonymous.holds(atom("bad", Var("X")))


def test_comparison_of_mixed_kinds_and_non_numeric_arithmetic_are_errors():
    solver = make_solver("bad :- '2024' > 3.\nbad2(X) :- X is a + 1.")
    with pytest.raises(HornError, match="数値同士か文字列同士"):
        solver.holds(atom("bad"))
    with pytest.raises(HornError, match="数値でない値を算術に使っています"):
        solver.holds(atom("bad2", Var("X")))


def test_depth_and_step_limits():
    loop = make_solver("p(X) :- p(X).")
    with pytest.raises(HornError, match="深さの上限（64）を超えました: p\\(a\\)"):
        loop.holds(atom("p", "a"))
    many = make_solver(
        "pairs(N) :- aggregate_all(count, (n(A), n(B)), N).",
        [("n", (i,)) for i in range(50)],
        max_steps=40,
    )
    with pytest.raises(HornError, match="手順数の上限（40）を超えました"):
        many.holds(atom("pairs", Var("N")))


# --- 層化・安全性・combine・listing ------------------------------------------


def test_stratification_error_on_negative_cycle():
    program = Program.parse("p :- \\+ q.\nq :- p.")
    with pytest.raises(HornError, match="層化できません"):
        program.strata()
    with pytest.raises(HornError, match="層化できません"):
        program.check()


def test_stratification_error_through_aggregate_and_forall():
    for text in (
        "p(N) :- aggregate_all(count, q(_), N).\nq(X) :- p(X).",
        "p :- forall(q(X), r(X)).\nr(X) :- s(X), p.",
    ):
        with pytest.raises(HornError, match="層化できません"):
            Program.parse(text).strata()


def test_strata_numbers_and_dependencies():
    program = Program.parse(
        "a(X) :- b(X), \\+ c(X).\n"
        "b(X) :- e(X).\n"
        "c(X) :- e(X), forall(f(X, Y), g(Y)).\n"
        "g(Y) :- h(Y).\n"
        "rec(X) :- e(X).\n"
        "rec(X) :- rec(X)."
    )
    assert program.dependencies("a/1") == [("b/1", False), ("c/1", True)]
    assert program.dependencies("c/1") == [("e/1", False), ("f/2", True), ("g/1", True)]
    strata = program.strata()
    assert strata["b/1"] == 0
    assert strata["g/1"] == 0
    assert strata["c/1"] == 1
    assert strata["a/1"] == 2
    assert strata["rec/1"] == 0  # 肯定の再帰は層化できる


def test_safety_errors():
    for text, variable in (
        ("p(X) :- q(a).", "X"),
        ("p(X) :- q(Y), \\+ r(X).", "X"),
        ("p(X, Y) :- q(X), Y > 3.", "Y"),
        ("p(_) :- q.", "_"),
        # forall・集約のゴールの内側の束縛は外へ出ないので、頭部の変数を束縛しない
        ("p(X) :- forall(q(Y), r(X, Y)).", "X"),
        ("p(S, T) :- aggregate_all(sum(K), (took(S, C), credits(C, K)), T).", "S"),
    ):
        program = Program.parse(text, origin="test")
        with pytest.raises(HornError, match=f"安全でない節です（頭部の変数 {variable} が本体で束縛されません）"):
            program.check()


def test_safety_accepts_is_equals_aggregate_results_and_positive_atoms():
    Program.parse(
        "a(X) :- X is 1 + 2.\n"
        "b(X) :- X = foo.\n"
        "c(S, T) :- student(S), aggregate_all(sum(K), (took(S, C), credits(C, K)), T).\n"
        "d(S, T) :- student(S), thesis(T), forall(prereq(T, C), done(S, C)).\n"  # B-5 の thesis_prerequisites_ok と同じ形
        "e(X) :- q(X)."
    ).check()


def test_combine_rejects_split_definitions():
    one = Program.parse("p(X) :- q(X).\np(X) :- r(X).", origin="rule-type/one")
    two = Program.parse("p(X) :- s(X).", origin="rule-type/two")
    with pytest.raises(HornError, match="述語 p/1 が rule-type/one と rule-type/two の2箇所で定義されています"):
        Program.combine([one, two])
    # 同じ origin 内の複数節（選言）と、別述語の合流はよい
    other = Program.parse("t(X) :- p(X).", origin="rule-type/two")
    combined = Program.combine([one, other])
    assert combined.defined() == {"p/1", "t/1"}
    assert len(combined.clauses_for("p/1")) == 2
    # 同じ名前でも引数の数が違えば別述語
    Program.combine([one, Program.parse("p(X, Y) :- q(X), q(Y).", origin="rule-type/two")])


def test_listing_in_depth_first_order():
    program = Program.combine(
        [
            Program.parse(
                "top(S) :- left(S), right(S).\n"
                "right(S) :- shared(S, _).\n"
                "left(S) :- shared(S, X),\n"
                "    X > 1.",
                origin="a",
            ),
            Program.parse("shared(S, N) :- base(S, N).\nunrelated :- base(a, 1).", origin="b"),
        ]
    )
    assert program.listing(["top/1"]) == (
        "top(S) :- left(S), right(S).\n"
        "left(S) :- shared(S, X),\n"
        "    X > 1.\n"
        "shared(S, N) :- base(S, N).\n"
        "right(S) :- shared(S, _)."
    )
    assert program.listing(["right/1", "left/1"]).splitlines()[0] == "right(S) :- shared(S, _)."


# --- 形式表記 ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (31, "31"),
        (31.0, "31"),
        (3.5, "3.5"),
        (1 / 3, "0.333333"),
        (2.0000001, "2"),
        (True, "true"),
        (False, "false"),
        ("schema:AchieveAction", "schema:AchieveAction"),
        ("urdi:course/REQ-A", "urdi:course/REQ-A"),
        ("first", "first"),
        ("2024", "'2024'"),
        ("2026-04-15", "'2026-04-15'"),
        ("Upper", "'Upper'"),
        ("it's", "'it\\'s'"),
        ("a\\b", "'a\\\\b'"),
        ("山田太郎", "'山田太郎'"),
    ],
)
def test_format_constant(value, text):
    assert format_constant(value) == text


def test_format_literals():
    program = Program.parse(
        "f(S) :- schema:numberOfCredits(S, 4),\n"
        "    Minimum is ceiling(Required * Percent / 100),\n"
        "    X is A + B * C - (D - E),\n"
        "    Earned >= Minimum,\n"
        "    \\+ taken(S),\n"
        "    not((a(S), b(S))),\n"
        "    forall(req(P, C), done(S, C)),\n"
        "    aggregate_all(sum(K), (took(S, C2), credits(C2, K)), T),\n"
        "    aggregate_all(count, took(S, _), N),\n"
        "    aggregate_all(max(K3), credits(_, K3), M),\n"
        "    flag."
    )
    texts = [format_term(literal) for literal in program.clauses[0].body]
    assert texts == [
        "schema:numberOfCredits(S, 4)",
        "Minimum is ceiling(Required * Percent / 100)",
        "X is A + B * C - (D - E)",
        "Earned >= Minimum",
        "\\+ taken(S)",
        "\\+ (a(S), b(S))",
        "forall(req(P, C), done(S, C))",
        "aggregate_all(sum(K), (took(S, C2), credits(C2, K)), T)",
        "aggregate_all(count, took(S, _), N)",
        "aggregate_all(max(K3), credits(_, K3), M)",
        "flag",
    ]
    # 括弧は優先順位と左結合で必要なときだけ付ける
    for text in (
        "X is (A + B) * C",
        "X is A - (B + C)",
        "X is A - B + C",
        "X is A / (B * C)",
        "X is A * B / C",
        "X is max(0, L - R)",
        "X is (A - B) / (C + D)",
    ):
        parsed = Program.parse("f(X) :- " + text + ".").clauses[0].body[0]
        assert format_term(parsed) == text
    # 束縛の代入
    subst = {"Earned": 28, "Minimum": Var("M#3"), "M#3": 31}
    assert format_term(program.clauses[0].body[3], subst) == "28 >= 31"
    # 改名した変数は元の名前で表記する
    assert format_term(atom("p", Var("Student#12"), "2024")) == "p(Student, '2024')"


# --- 証明木 ------------------------------------------------------------------


EXPLAIN_PROGRAM = """
ok(S) :- year_ok(S), credits_ok(S), prereq_ok(S).
year_ok(S) :- year(S, Y), Y >= 3.
credits_ok(S) :- year(S, _), aggregate_all(sum(K), (took(S, C), credits(C, K)), T), T >= 6.
prereq_ok(S) :- year(S, _), forall(prereq(C), took(S, C)).
"""

EXPLAIN_FACTS = [
    ("year", ("s1", 4)),
    ("year", ("s2", 4)),
    ("year", ("s3", 2)),
    ("credits", ("A", 4)),
    ("credits", ("B", 2)),
    ("prereq", ("A",)),
    ("prereq", ("B",)),
    ("took", ("s1", "A")),
    ("took", ("s1", "B")),
    ("took", ("s2", "A")),
    ("took", ("s3", "A")),
    ("took", ("s3", "B")),
]


def test_explain_success_tree():
    solver = make_solver(EXPLAIN_PROGRAM, EXPLAIN_FACTS)
    proof = solver.explain(atom("ok", "s1"))
    assert proof.proved and proof.kind == "rule" and proof.goal == "ok(s1)"
    assert proof.clause == "ok(S) :- year_ok(S), credits_ok(S), prereq_ok(S)."
    year, credits, prereq = proof.children
    assert [child.goal for child in year.children] == ["year(s1, 4)", "4 >= 3"]
    assert [child.kind for child in year.children] == ["fact", "builtin"]
    aggregate = credits.children[1]
    assert aggregate.kind == "aggregate"
    assert aggregate.detail == "2件"
    assert aggregate.goal == "aggregate_all(sum(K), (took(s1, C), credits(C, K)), 6)"
    assert [child.goal for child in aggregate.children] == [
        "(took(s1, 'A'), credits('A', 4))",
        "(took(s1, 'B'), credits('B', 2))",
    ]
    assert aggregate.children[0].kind == "conj"
    forall = prereq.children[1]
    assert forall.kind == "forall" and forall.proved
    assert forall.goal == "forall(prereq(C), took(s1, C))"
    # forall の子は、条件の各解についての A の代入形
    assert [child.goal for child in forall.children] == ["took(s1, 'A')", "took(s1, 'B')"]
    assert all(child.proved and child.kind == "fact" for child in forall.children)
    data = proof.to_dict()
    assert set(data) == {"goal", "kind", "proved", "clause", "detail", "children"}
    assert data["children"][0]["children"][1] == {
        "goal": "4 >= 3",
        "kind": "builtin",
        "proved": True,
        "clause": None,
        "detail": None,
        "children": [],
    }


def test_explain_failure_shows_longest_prefix_and_forall_children():
    solver = make_solver(EXPLAIN_PROGRAM, EXPLAIN_FACTS)
    # s2: 学年・単位はよいが前提科目 B が無い
    proof = solver.explain(atom("ok", "s2"))
    assert not proof.proved and proof.kind == "rule"
    assert proof.clause == "ok(S) :- year_ok(S), credits_ok(S), prereq_ok(S)."
    assert [(c.goal, c.proved) for c in proof.children] == [
        ("year_ok(s2)", True),
        ("credits_ok(s2)", False),
    ]
    # credits_ok の失敗は、集約までは進み、比較で止まった
    credits = proof.children[1]
    assert [(c.goal, c.kind, c.proved) for c in credits.children] == [
        ("year(s2, 4)", "fact", True),
        ("aggregate_all(sum(K), (took(s2, C), credits(C, K)), 4)", "aggregate", True),
        ("4 >= 6", "builtin", False),
    ]
    # s3: 学年で止まる（最初のリテラルの失敗）
    proof = solver.explain(atom("ok", "s3"))
    year = proof.children[0]
    assert len(proof.children) == 1
    assert [(c.goal, c.proved) for c in year.children] == [("year(s3, 2)", True), ("2 >= 3", False)]
    # forall の失敗は、成功・失敗の両方の子を並べる
    forall_fail = solver.explain(atom("prereq_ok", "s2"))
    forall = forall_fail.children[1]
    assert forall.kind == "forall" and not forall.proved
    assert forall.goal == "forall(prereq(C), took(s2, C))"
    assert [(c.goal, c.kind, c.proved, c.detail) for c in forall.children] == [
        ("took(s2, 'A')", "fact", True, None),
        ("took(s2, 'B')", "fact", False, "事実がない"),
    ]


def test_explain_failure_picks_the_clause_that_went_furthest():
    solver = make_solver(
        "elig(S) :- a(S), b(S).\n"
        "elig(S) :- a(S), c(S, X), d(X).\n"
        "elig(S) :- e(S).",
        [("a", ("s",)), ("c", ("s", 1)), ("c", ("s", 2)), ("d", (3,))],
    )
    proof = solver.explain(atom("elig", "s"))
    # 2番目の節が2リテラル進めたので採用（バックトラックで c の別解も試したうえで）
    assert proof.clause == "elig(S) :- a(S), c(S, X), d(X)."
    assert [(c.goal, c.proved) for c in proof.children] == [
        ("a(s)", True),
        ("c(s, 1)", True),
        ("d(1)", False),
    ]
    assert proof.children[2].detail == "事実がない"


def test_explain_failure_tie_prefers_earlier_clause_and_no_matching_clause():
    solver = make_solver("p(S) :- a(S), b(S).\np(S) :- a(S), c(S).\nq(x) :- a(x).", [("a", ("s",))])
    assert solver.explain(atom("p", "s")).children[1].goal == "b(s)"
    no_clause = solver.explain(atom("q", "y"))
    assert (no_clause.kind, no_clause.proved, no_clause.detail, no_clause.children) == (
        "rule",
        False,
        "該当する節がない",
        [],
    )
    missing = solver.explain(atom("schema:numberOfCredits", "c", Var("K")))
    assert (missing.goal, missing.kind, missing.detail) == ("schema:numberOfCredits(c, K)", "fact", "事実がない")


def test_explain_failure_of_negation_shows_the_proof_of_its_target():
    solver = make_solver("free(S) :- item(S), \\+ taken(S).", [("item", ("a",)), ("taken", ("a",))])
    proof = solver.explain(atom("free", "a"))
    negation = proof.children[1]
    assert (negation.goal, negation.kind, negation.proved) == ("\\+ taken(a)", "not", False)
    assert [(c.goal, c.proved) for c in negation.children] == [("taken(a)", True)]
    ok = make_solver("free(S) :- item(S), \\+ taken(S).", [("item", ("b",))]).explain(atom("free", "b"))
    assert ok.proved and ok.children[1].kind == "not" and ok.children[1].proved


def test_explain_failure_of_max_aggregate_on_empty_set():
    solver = make_solver("top(S, M) :- s(S), aggregate_all(max(K), k(S, K), M).", [("s", ("x",))])
    proof = solver.explain(atom("top", "x", Var("M")))
    failed = proof.children[1]
    assert (failed.kind, failed.proved, failed.detail, failed.children) == ("aggregate", False, "0件", [])
    assert failed.goal == "aggregate_all(max(K), k(x, K), M)"


# --- JSON-LD ------------------------------------------------------------------


def test_from_jsonld():
    facts = FactBase.from_jsonld(
        [
            {
                "@id": "urdi:student/S001",
                "@type": ["schema:Person", "ccso:UndergraduateStudent"],
                "schema:name": "山田太郎",
                "urd:yearOfStudy": 3,
                "ccso:enrolledIn": {"@id": "urdi:program/engineering-2026"},
                "ccso:hasCompleted": [{"@id": "urdi:course/REQ-A"}, {"@id": "urdi:course/GEN-01"}],
                "urd:active": True,
                "schema:startDate": {"@value": "2024-04-01", "@type": "xsd:date"},
                "schema:address": {"schema:postalCode": "194-8610"},
            },
            {"@id": "urdi:course/REQ-A", "@type": "schema:Course", "schema:numberOfCredits": 4},
        ]
    )
    assert facts.match("rdf:type", 2) == [
        ("urdi:student/S001", "schema:Person"),
        ("urdi:student/S001", "ccso:UndergraduateStudent"),
        ("urdi:course/REQ-A", "schema:Course"),
    ]
    assert facts.match("ccso:hasCompleted", 2, "urdi:student/S001") == [
        ("urdi:student/S001", "urdi:course/REQ-A"),
        ("urdi:student/S001", "urdi:course/GEN-01"),
    ]
    assert facts.match("ccso:enrolledIn", 2) == [("urdi:student/S001", "urdi:program/engineering-2026")]
    assert facts.match("schema:name", 2) == [("urdi:student/S001", "山田太郎")]
    assert facts.match("urd:yearOfStudy", 2) == [("urdi:student/S001", 3)]
    assert facts.match("urd:active", 2) == [("urdi:student/S001", True)]
    assert facts.match("schema:startDate", 2) == [("urdi:student/S001", "2024-04-01")]
    assert facts.match("schema:address", 2) == []
    assert facts.match("schema:numberOfCredits", 2, "urdi:course/REQ-A") == [("urdi:course/REQ-A", 4)]
    assert facts.match("schema:numberOfCredits", 2, "urdi:course/none") == []


def test_fact_base_ignores_exact_duplicates():
    facts = FactBase([("p", ("a", 1)), ("p", ("a", 1)), ("p", ("a", True))])
    assert facts.match("p", 2) == [("a", 1), ("a", True)]


# --- 統合：B-4/B-5 の確定プログラム ------------------------------------------

EARNED_CREDITS = """% urd:earnedCredits の定義
urd:earnedCredits(Student, Total) :-
    rdf:type(Student, schema:Person),
    aggregate_all(sum(Credits),
                  (ccso:hasCompleted(Student, Course), schema:numberOfCredits(Course, Credits)),
                  Total).
"""

FIRST_YEAR = """% 履修規程 第14条：第1年次に、卒業に必要な単位数の4分の1以上を修得する
meets_first_year_requirement(Student) :-
    first_year_credits(Student, Earned),
    first_year_minimum(Student, Minimum),
    Earned >= Minimum.

% 対象年次に修得した単位数（修得の記録をたどって合計する）
first_year_credits(Student, Earned) :-
    ccso:enrolledIn(Student, Program),
    urd:targetYearOfStudy(Program, Year),
    aggregate_all(sum(Credits),
                  (completed_in_year(Student, Year, Course),
                   schema:numberOfCredits(Course, Credits)),
                  Earned).

% 必要な単位数＝卒業に必要な単位数 × 割合（端数は切り上げ）
first_year_minimum(Student, Minimum) :-
    ccso:enrolledIn(Student, Program),
    schema:numberOfCredits(Program, Required),
    urd:minimumCreditRatio(Program, Percent),
    Minimum is ceiling(Required * Percent / 100).

% 学生がその学年のときに修得した科目
completed_in_year(Student, Year, Course) :-
    schema:agent(Record, Student),
    rdf:type(Record, schema:AchieveAction),
    urd:atYearOfStudy(Record, Year),
    schema:object(Record, Course).
"""

THESIS = """eligible_to_register(Student, Thesis) :-
    thesis_year_ok(Student, Thesis),
    thesis_credits_ok(Student, Thesis),
    thesis_prerequisites_ok(Student, Thesis).

thesis_year_ok(Student, Thesis) :-
    urd:minimumYearOfStudy(Thesis, MinYear),
    urd:yearOfStudy(Student, Year),
    Year >= MinYear.

thesis_credits_ok(Student, Thesis) :-
    urd:minimumEarnedCredits(Thesis, MinCredits),
    urd:earnedCredits(Student, Earned),
    Earned >= MinCredits.

thesis_prerequisites_ok(Student, Thesis) :-
    rdf:type(Student, schema:Person),
    rdf:type(Thesis, schema:Course),
    forall(schema:coursePrerequisites(Thesis, Course),
           ccso:hasCompleted(Student, Course)).
"""


def integration_nodes(year1_courses: list[str], year2_courses: list[str]) -> list[dict]:
    """学生1人・科目数件・年度ごとの AchieveAction の小さなグラフ。"""
    credits = {"REQ-A": 4, "REQ-B": 4, "GEN-01": 2, "BIG-1": 13, "BIG-2": 12, "BIG-3": 60, "THESIS": 8}
    nodes: list[dict] = [
        {
            "@id": "urdi:program/engineering-2026",
            "@type": ["schema:EducationalOccupationalProgram", "ccso:ProgramofStudy"],
            "schema:numberOfCredits": 124,
            "urd:targetYearOfStudy": 1,
            "urd:minimumCreditRatio": 25,
        },
        {
            "@id": "urdi:course/THESIS",
            "@type": ["schema:Course", "ccso:Course"],
            "schema:numberOfCredits": 8,
            "urd:minimumYearOfStudy": 4,
            "urd:minimumEarnedCredits": 100,
            "schema:coursePrerequisites": [{"@id": "urdi:course/REQ-A"}, {"@id": "urdi:course/REQ-B"}],
        },
    ]
    for code, value in credits.items():
        if code != "THESIS":
            nodes.append(
                {"@id": f"urdi:course/{code}", "@type": ["schema:Course", "ccso:Course"], "schema:numberOfCredits": value}
            )
    completed = year1_courses + year2_courses
    nodes.append(
        {
            "@id": "urdi:student/T001",
            "@type": ["schema:Person", "ccso:UndergraduateStudent"],
            "schema:name": "例題の学生",
            "ccso:enrolledIn": {"@id": "urdi:program/engineering-2026"},
            "urd:yearOfStudy": 4,
            "ccso:hasCompleted": [{"@id": f"urdi:course/{code}"} for code in completed],
        }
    )
    for year, (academic_year, courses) in enumerate((("2023", year1_courses), ("2024", year2_courses)), start=1):
        nodes.append(
            {
                "@id": f"urdi:achievement/T001/{academic_year}",
                "@type": "schema:AchieveAction",
                "schema:agent": {"@id": "urdi:student/T001"},
                "schema:object": [{"@id": f"urdi:course/{code}"} for code in courses],
                "schema:actionStatus": {"@id": "schema:CompletedActionStatus"},
                "ccso:academicYear": academic_year,
                "urd:atYearOfStudy": year,
            }
        )
    return nodes


def integration_program() -> Program:
    program = Program.combine(
        [
            Program.parse(EARNED_CREDITS, origin="urd:earnedCredits"),
            Program.parse(FIRST_YEAR, origin="rule-type/first_year_credit_requirement"),
            Program.parse(THESIS, origin="rule-type/thesis_eligibility"),
        ]
    )
    program.check()
    return program


def test_integration_with_the_fixed_programs_of_b4_and_b5():
    program = integration_program()
    assert program.strata()["first_year_credits/2"] == 1
    assert program.strata()["meets_first_year_requirement/1"] == 1
    student = "urdi:student/T001"
    thesis = "urdi:course/THESIS"

    # 1年次 4+2+13+12=31（ちょうど必要数）、2年次 4+60=64 → 合計 95
    passing = Solver(
        program,
        FactBase.from_jsonld(integration_nodes(["REQ-A", "GEN-01", "BIG-1", "BIG-2"], ["REQ-B", "BIG-3"])),
    )
    assert passing.value(atom("first_year_credits", student, Var("E")), "E") == 31
    minimum = passing.value(atom("first_year_minimum", student, Var("M")), "M")
    assert minimum == 31 and isinstance(minimum, int)
    assert passing.value(atom("urd:earnedCredits", student, Var("T")), "T") == 95
    assert passing.holds(atom("meets_first_year_requirement", student))
    # 学年・前提科目はよいが修得単位が 95 < 100
    assert not passing.holds(atom("eligible_to_register", student, thesis))
    assert passing.holds(atom("thesis_year_ok", student, thesis))
    assert passing.holds(atom("thesis_prerequisites_ok", student, thesis))

    proof = passing.explain(atom("meets_first_year_requirement", student))
    assert proof.proved and proof.goal == "meets_first_year_requirement(urdi:student/T001)"
    assert [c.goal for c in proof.children] == [
        "first_year_credits(urdi:student/T001, 31)",
        "first_year_minimum(urdi:student/T001, 31)",
        "31 >= 31",
    ]
    aggregate = proof.children[0].children[2]
    assert aggregate.kind == "aggregate" and aggregate.detail == "4件"
    assert aggregate.children[0].goal == (
        "(completed_in_year(urdi:student/T001, 1, urdi:course/REQ-A), "
        "schema:numberOfCredits(urdi:course/REQ-A, 4))"
    )
    completed = aggregate.children[0].children[0]
    assert [c.goal for c in completed.children] == [
        "schema:agent(urdi:achievement/T001/2023, urdi:student/T001)",
        "rdf:type(urdi:achievement/T001/2023, schema:AchieveAction)",
        "urd:atYearOfStudy(urdi:achievement/T001/2023, 1)",
        "schema:object(urdi:achievement/T001/2023, urdi:course/REQ-A)",
    ]
    assert proof.children[1].children[3].goal == "31 is ceiling(124 * 25 / 100)"

    thesis_proof = passing.explain(atom("eligible_to_register", student, thesis))
    assert not thesis_proof.proved
    assert [(c.goal, c.proved) for c in thesis_proof.children] == [
        ("thesis_year_ok(urdi:student/T001, urdi:course/THESIS)", True),
        ("thesis_credits_ok(urdi:student/T001, urdi:course/THESIS)", False),
    ]
    assert [(c.goal, c.proved) for c in thesis_proof.children[1].children] == [
        ("urd:minimumEarnedCredits(urdi:course/THESIS, 100)", True),
        ("urd:earnedCredits(urdi:student/T001, 95)", True),
        ("95 >= 100", False),
    ]

    # 1年次 4+12+12... のかわりに 28 単位（REQ-A を2年次へ回す）、2年次に残りを修得
    failing = Solver(
        program,
        FactBase.from_jsonld(
            integration_nodes(["GEN-01", "BIG-1", "BIG-2"], ["REQ-A", "REQ-B", "BIG-3"])
        ),
    )
    assert failing.value(atom("first_year_credits", student, Var("E")), "E") == 27
    assert not failing.holds(atom("meets_first_year_requirement", student))
    failed = failing.explain(atom("meets_first_year_requirement", student))
    assert not failed.proved and failed.kind == "rule"
    assert [(c.goal, c.kind, c.proved) for c in failed.children] == [
        ("first_year_credits(urdi:student/T001, 27)", "rule", True),
        ("first_year_minimum(urdi:student/T001, 31)", "rule", True),
        ("27 >= 31", "builtin", False),
    ]

    # 前提科目の forall：REQ-B が無いと失敗し、子に成功・失敗が並ぶ
    no_req_b = Solver(
        program,
        FactBase.from_jsonld(integration_nodes(["REQ-A", "GEN-01", "BIG-1", "BIG-2"], ["BIG-3"])),
    )
    prereq = no_req_b.explain(atom("thesis_prerequisites_ok", student, thesis)).children[2]
    assert prereq.kind == "forall" and not prereq.proved
    assert [(c.goal, c.proved) for c in prereq.children] == [
        ("ccso:hasCompleted(urdi:student/T001, urdi:course/REQ-A)", True),
        ("ccso:hasCompleted(urdi:student/T001, urdi:course/REQ-B)", False),
    ]

    # 卒業研究の要件をすべて満たす学生（1年次 31、2年次 4+60+... で 100 以上）
    eligible_nodes = integration_nodes(["REQ-A", "GEN-01", "BIG-1", "BIG-2"], ["REQ-B", "BIG-3"])
    eligible_facts = FactBase.from_jsonld(eligible_nodes)
    eligible_facts.add("ccso:hasCompleted", (student, "urdi:course/EXTRA"))
    eligible_facts.add("schema:numberOfCredits", ("urdi:course/EXTRA", 5))
    eligible = Solver(program, eligible_facts)
    assert eligible.value(atom("urd:earnedCredits", student, Var("T")), "T") == 100
    assert eligible.holds(atom("eligible_to_register", student, thesis))
    eligible_proof = eligible.explain(atom("eligible_to_register", student, thesis))
    assert eligible_proof.proved
    assert eligible_proof.clause == (
        "eligible_to_register(Student, Thesis) :- thesis_year_ok(Student, Thesis), "
        "thesis_credits_ok(Student, Thesis), thesis_prerequisites_ok(Student, Thesis)."
    )
    assert [c.proved for c in eligible_proof.children] == [True, True, True]

    # listing は到達できる節すべて（派生用語の定義節も含む）
    listing = program.listing(["eligible_to_register/2"])
    assert listing.splitlines()[0] == "eligible_to_register(Student, Thesis) :-"
    assert "urd:earnedCredits(Student, Total) :-" in listing
    assert "first_year" not in listing
