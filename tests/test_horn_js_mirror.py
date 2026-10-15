"""Horn 節の処理系の JS 版（docs/assets/horn.js）が src/horn.py と同じ結果を出すことを検証する。

tests/test_horn.py の主要ケース（構文エラーの文言・形式表記・安全性と層化の検査・listing・
解・証明木）をここで列挙して JSON で Node.js へ渡し、JS の結果を Python の結果と突き合わせる。
期待値は Python の処理系にその場で計算させるので、ここに文字列を二重に書かない。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.horn import FactBase, HornError, Program, Solver, _Parser, format_constant, format_term

ROOT = Path(__file__).resolve().parents[1]
HORN_JS = ROOT / "docs" / "assets" / "horn.js"
HARNESS = ROOT / "tests" / "js" / "check_horn.mjs"

SYNTAX = [
    # 構文エラー（tests/test_horn.py の test_syntax_errors_report_origin_and_line）
    ["rule-type/demo", "p(a).\nq(X) :- p(X)\nr."],
    ["rule-type/demo", "p(a).\n\nq(X) :- X > ."],
    ["rule-type/demo", "p :- q(X) >= 3."],
    ["rule-type/demo", "p :- q ; r."],
    ["rule-type/demo", "p :- X is foo(1)."],
    ["rule-type/demo", "p('abc)."],
    ["rule-type/demo", "p('a\\nb')."],
    ["rule-type/demo", "p :- 3 is X."],
    ["rule-type/demo", "X :- p."],
    ["rule-type/demo", "p(f(a))."],
    ["", "p :- max(1)."],
    ["", "p :- aggregate_all(avg(X), q(X), N)."],
    ["", "p :- q('\\"],
    # 解釈できない文字の repr（引用符・バックスラッシュ・全角空白・制御文字）
    ["", "p :- q(a) ` r."],
    ["", "p :- \\ q."],
    ["", "p :-\u3000q."],
    ["", "p :- q\u0007."],
    ["", "p :- q(a) \u00e9."],
    # 正しい構文（形式表記と原文の切り出し）
    [
        "rule-type/x",
        "\n% 先頭のコメント\nreq(urdi:course/REQ-A).   % 行末のコメント\n"
        "done(S) :- ccso:hasCompleted(S, urdi:course/REQ-A),\n           rdf:type(S, schema:Person).\n",
    ],
    ["", "p('it\\'s', \"a\\\\b\", 'x\"y', 12, 3.5).\nflag.\n"
         "q :- flag, X is 1 + 2 * 3, X >= 7, '2026-04-15' =< '2026-04-30'."],
    [
        "",
        "r(S) :- s(S), \\+ t(S), not((u(S), v(S))),\n"
        "    forall(w(S, C), x(S, C)),\n"
        "    aggregate_all(count, w(S, _), N), aggregate_all(sum(K), (w(S, C2), k(C2, K)), T),\n"
        "    aggregate_all(max(K2), k(_, K2), M), M2 is max(N, T) - ceiling(M / 2), M2 \\= 0.",
    ],
    [
        "",
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
        "    flag.",
    ],
    ["", "f(X) :- X is (A + B) * C, X is A - (B + C), X is A - B + C, X is A / (B * C),"
         " X is A * B / C, X is max(0, L - R), X is (A - B) / (C + D)."],
    ["", "p:-q.\nq."],
    ["", "p :- q(1.50, 007, 'Upper', \"2024\", first, schema:AchieveAction)."],
    # 全角数字も Python の \\d と同じく数として読む
    ["", "p :- q(\uff11\uff12)."],
]

CONSTANTS = [
    31, 31.0, 3.5, 1 / 3, 2.0000001, 0.00001234, 0.0001, 123456.5, -2.25, 1e22, 0,
    True, False,
    "schema:AchieveAction", "urdi:course/REQ-A", "first", "2024", "2026-04-15",
    "Upper", "it's", "a\\b", "山田太郎", "", "a b",
]

EXPLAIN_PROGRAM = """
ok(S) :- year_ok(S), credits_ok(S), prereq_ok(S).
year_ok(S) :- year(S, Y), Y >= 3.
credits_ok(S) :- year(S, _), aggregate_all(sum(K), (took(S, C), credits(C, K)), T), T >= 6.
prereq_ok(S) :- year(S, _), forall(prereq(C), took(S, C)).
"""

EXPLAIN_FACTS = [
    ["year", ["s1", 4]],
    ["year", ["s2", 4]],
    ["year", ["s3", 2]],
    ["credits", ["A", 4]],
    ["credits", ["B", 2]],
    ["prereq", ["A"]],
    ["prereq", ["B"]],
    ["took", ["s1", "A"]],
    ["took", ["s1", "B"]],
    ["took", ["s2", "A"]],
    ["took", ["s3", "A"]],
    ["took", ["s3", "B"]],
]

CHECKS = [
    [["test", "p(X) :- q(a)."]],
    [["test", "p(X) :- q(Y), \\+ r(X)."]],
    [["test", "p(X, Y) :- q(X), Y > 3."]],
    [["test", "p(_) :- q."]],
    [["test", "p(X) :- forall(q(Y), r(X, Y))."]],
    [["test", "p(S, T) :- aggregate_all(sum(K), (took(S, C), credits(C, K)), T)."]],
    [["test", "p :- \\+ q.\nq :- p."]],
    [["test", "p(N) :- aggregate_all(count, q(_), N).\nq(X) :- p(X)."]],
    [["test", "p :- forall(q(X), r(X)).\nr(X) :- s(X), p."]],
    [[
        "test",
        "a(X) :- b(X), \\+ c(X).\nb(X) :- e(X).\nc(X) :- e(X), forall(f(X, Y), g(Y)).\n"
        "g(Y) :- h(Y).\nrec(X) :- e(X).\nrec(X) :- rec(X).",
    ]],
    [["test", "a(X) :- X is 1 + 2.\nb(X) :- X = foo.\n"
              "c(S, T) :- student(S), aggregate_all(sum(K), (took(S, C), credits(C, K)), T).\n"
              "d(S, T) :- student(S), thesis(T), forall(prereq(T, C), done(S, C)).\ne(X) :- q(X)."]],
    [["rule-type/one", "p(X) :- q(X).\np(X) :- r(X)."], ["rule-type/two", "p(X) :- s(X)."]],
    [["rule-type/one", "p(X) :- q(X).\np(X) :- r(X)."], ["rule-type/two", "t(X) :- p(X)."]],
]

LISTINGS = [
    [
        [
            ["a", "top(S) :- left(S), right(S).\nright(S) :- shared(S, _).\n"
                  "left(S) :- shared(S, X),\n    X > 1."],
            ["b", "shared(S, N) :- base(S, N).\nunrelated :- base(a, 1)."],
        ],
        ["top/1"],
    ],
    [[["a", "top(S) :- left(S), right(S).\nright(S) :- shared(S, _).\nleft(S) :- shared(S, _)."]],
     ["right/1", "left/1"]],
]


def query(program, goal, facts=(), mode="solve", max_depth=64, max_steps=200_000):
    if isinstance(program, str):
        program = [["test", program]]
    return {
        "program": program,
        "facts": [list(fact) for fact in facts],
        "goal": goal,
        "mode": mode,
        "max_depth": max_depth,
        "max_steps": max_steps,
    }


QUERIES = [
    query("same(X) :- pair(X, X).", "same(X)", [["pair", ["a", "a"]], ["pair", ["a", "b"]]]),
    query(
        "eq(X, Y) :- item(X), item(Y), X = Y.\nneq(X, Y) :- item(X), item(Y), X \\= Y.",
        "neq(a, Y)",
        [["item", ["a"]], ["item", ["b"]]],
    ),
    query("p :- v(n, 2).\nq :- v(b, 1).\nr :- v(b, true).",
          "(p, \\+ q, r)", [["v", ["n", 2]], ["v", ["b", True]], ["v", ["s", "2"]]], "holds"),
    query("ok(X) :- a(X).\nok(X) :- b(X).", "ok(X)", [["a", ["x1"]], ["b", ["x2"]], ["ok", ["x0"]]]),
    query("free(X) :- item(X), \\+ taken(X).", "free(X)", [["item", ["a"]], ["item", ["b"]], ["taken", ["a"]]]),
    query("bad(X) :- item(X), \\+ taken(Y).", "bad(X)", [["item", ["a"]]]),
    query("lonely(X) :- item(X), \\+ link(X, _).", "lonely(X)",
          [["item", ["a"]], ["item", ["b"]], ["link", ["a", "z"]]]),
    query(
        "total(S, T) :- student(S), aggregate_all(sum(K), (took(S, C), credits(C, K)), T).\n"
        "n(S, N) :- student(S), aggregate_all(count, took(S, _), N).\n"
        "top(S, M) :- student(S), aggregate_all(max(K), (took(S, C), credits(C, K)), M).\n"
        "low(S, M) :- student(S), aggregate_all(min(K), (took(S, C), credits(C, K)), M).\n"
        "all(S, T, N, M, L) :- total(S, T), n(S, N), top(S, M), low(S, L).",
        "all(S, T, N, M, L)",
        [["student", ["s1"]], ["student", ["s9"]], ["took", ["s1", "c1"]], ["took", ["s1", "c2"]],
         ["took", ["s1", "c3"]], ["credits", ["c1", 2]], ["credits", ["c2", 2]], ["credits", ["c3", 4]]],
    ),
    query(
        "half(X) :- X is 62 / 2.\nfrac(X) :- X is 7 / 2.\nneed(X) :- X is ceiling(124 * 25 / 100).\n"
        "rnd(X) :- X is round(2.5) + floor(1.9) + abs(0 - 3) + min(4, 5) - max(1, 2).\n"
        "neg(X) :- X is round(0 - 2.5).\n"
        "all(A, B, C, D, E) :- half(A), frac(B), need(C), rnd(D), neg(E).",
        "all(A, B, C, D, E)",
    ),
    query("third(X) :- X is 1 / 3.", "third(X)", mode="explain"),
    query("zero(X) :- X is 1 / (2 - 2).", "zero(X)"),
    query("early(D) :- applied(D), deadline(L), D =< L.", "early(D)",
          [["deadline", ["2026-04-15"]], ["applied", ["2026-04-01"]], ["applied", ["2026-04-16"]]]),
    query("bad(X) :- val(X), Y > X.", "bad(X)", [["val", [1]]]),
    query("bad(X) :- val(X), X > _.", "bad(X)", [["val", [1]]]),
    query("bad :- '2024' > 3.", "bad"),
    query("bad2(X) :- X is a + 1.", "bad2(X)"),
    query("p(X) :- p(X).", "p(a)"),
    query("pairs(N) :- aggregate_all(count, (n(A), n(B)), N).", "pairs(N)",
          [["n", [i]] for i in range(50)], max_steps=40),
    query(EXPLAIN_PROGRAM, "ok(s1)", EXPLAIN_FACTS, "explain"),
    query(EXPLAIN_PROGRAM, "ok(s2)", EXPLAIN_FACTS, "explain"),
    query(EXPLAIN_PROGRAM, "ok(s3)", EXPLAIN_FACTS, "explain"),
    query(EXPLAIN_PROGRAM, "prereq_ok(s2)", EXPLAIN_FACTS, "explain"),
    query(
        "elig(S) :- a(S), b(S).\nelig(S) :- a(S), c(S, X), d(X).\nelig(S) :- e(S).",
        "elig(s)",
        [["a", ["s"]], ["c", ["s", 1]], ["c", ["s", 2]], ["d", [3]]],
        "explain",
    ),
    query("p(S) :- a(S), b(S).\np(S) :- a(S), c(S).\nq(x) :- a(x).", "p(s)", [["a", ["s"]]], "explain"),
    query("q(x) :- a(x).", "q(y)", [["a", ["s"]]], "explain"),
    query("q(x) :- a(x).", "schema:numberOfCredits(c, K)", [], "explain"),
    query("free(S) :- item(S), \\+ taken(S).", "free(a)", [["item", ["a"]], ["taken", ["a"]]], "explain"),
    query("free(S) :- item(S), \\+ taken(S).", "free(b)", [["item", ["b"]]], "explain"),
    query("top(S, M) :- s(S), aggregate_all(max(K), k(S, K), M).", "top(x, M)", [["s", ["x"]]], "explain"),
    query("big(N) :- n(N), N > 1.", "(big(X), big(Y), X \\= Y)", [["n", [1]], ["n", [2]], ["n", [3.5]]], "explain"),
    query("ratio(X) :- X is 1 / 100000.", "ratio(X)", mode="explain"),
]


def run_python(cases):
    def attempt(action):
        try:
            return {"ok": action()}
        except HornError as error:
            return {"error": str(error)}

    def program_of(sources):
        return Program.combine([Program.parse(text, origin=origin) for origin, text in sources])

    def run_query(case):
        def action():
            program = program_of(case["program"])
            program.check()
            facts = FactBase([(predicate, tuple(args)) for predicate, args in case["facts"]])
            solver = Solver(program, facts, case["max_depth"], case["max_steps"])
            goal = _Parser(case["goal"], "").parse_goal_arg()
            if case["mode"] == "explain":
                return solver.explain(goal).to_dict()
            if case["mode"] == "holds":
                return solver.holds(goal)
            return list(solver.solve(goal))

        return attempt(action)

    def parse(origin, text):
        program = Program.parse(text, origin=origin)
        return [
            [format_term(clause.head), [format_term(literal) for literal in clause.body], clause.source]
            for clause in program.clauses
        ]

    def check(sources):
        program = program_of(sources)
        program.check()
        return {"defined": sorted(program.defined()), "strata": program.strata()}

    return {
        "syntax": [attempt(lambda o=origin, t=text: parse(o, t)) for origin, text in cases["syntax"]],
        "constants": [format_constant(value) for value in cases["constants"]],
        "checks": [attempt(lambda s=sources: check(s)) for sources in cases["checks"]],
        "listings": [
            attempt(lambda s=sources, k=keys: program_of(s).listing(k))
            for sources, keys in cases["listings"]
        ],
        "queries": [run_query(case) for case in cases["queries"]],
    }


def test_horn_js_matches_the_python_interpreter(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js が無いため horn.js の照合をスキップします。")
    cases = {
        "syntax": SYNTAX,
        "constants": CONSTANTS,
        "checks": CHECKS,
        "listings": LISTINGS,
        "queries": QUERIES,
    }
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [node, str(HARNESS), str(HORN_JS), str(cases_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    # JSON を経由した値で Python 側も評価する（1e22 などの数の扱いを揃えるため）
    expected = json.loads(json.dumps(run_python(json.loads(cases_path.read_text(encoding="utf-8")))))
    for section in ("syntax", "constants", "checks", "listings", "queries"):
        for index, (js, py) in enumerate(zip(actual[section], expected[section])):
            assert js == py, f"{section}[{index}]: {cases[section][index]!r}"
        assert len(actual[section]) == len(expected[section]), section
    # 構文エラーのケースが本当にエラーになっていること（照合が空振りしていない）
    assert sum("error" in row for row in expected["syntax"]) >= 15
    assert sum("error" in row for row in expected["queries"]) >= 8
