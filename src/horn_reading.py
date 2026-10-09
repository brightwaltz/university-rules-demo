"""Horn 節と証明木の読み下し（決定論的なテンプレート展開）。

規程の担当者が形式表記を読まなくても済むように、節の各リテラルを日本語の一文へ
展開する。生成 AI は使わない。規則は次のものだけで、JS 版へそのまま移植できるように
している。

リテラルの型
- 節を持つ述語: その rule-type の ``readings`` の型（``{1}`` ``{2}`` … は引数）
- ``rdf:type(A, B)``: 「{A}は{B}である」
- 用語（CURIE、引数2つ）: term ノードの ``reading``（``{1}`` ``{2}``）。
  無ければ「{1}の{label}は{2}」
- それ以外の述語: 「述語(引数, …)」（引数は呼び名）
- 連言: 各リテラルの読みを「、かつ」で結ぶ
- 比較: 「{L}が{R}以上／より大きい／以下／未満／と等しい／と等しくない」
- ``is``: 「{V}は {式} で求める」（式は形式表記。変数・定数だけ呼び名にする）
- ``aggregate_all``: sum「{G}、であるような{T}の合計は{R}」、
  count「{G}、であるようなものの件数は{R}」、max・min は「合計」を「最大値」「最小値」に
- ``forall(C, A)``: 「{C}、であるものすべてについて、{A}」
- ``\\+ G``: 「{G}、ということはない」
- 例外は1つだけ: 集約の {G}・forall の {C} の読みが「である」で終わるときは、
  続く「、である」を省いてつなぐ（「…である、であるもの」を「…であるもの」にする）

引数の呼び名
- 変数: 〈呼び名〉。呼び名はその節を定義した rule-type（または term ノード）の
  ``variables``、無ければ変数名。証明木で未束縛のまま残った変数も同じ
- 定数: IRI でナレッジグラフに schema:name があればその名前、グラフ文書の term の
  CURIE ならその label、それ以外は形式表記のまま
"""

from __future__ import annotations

import re
from typing import Any

from src.horn import (
    Aggregate,
    Atom,
    BinOp,
    Clause,
    Compare,
    Conj,
    Forall,
    Func,
    HornError,
    Is,
    Not,
    Var,
    _Parser,
    format_constant,
    format_term,
)
from src.knowledge_graph import KnowledgeGraph
from src.ontology import GRAPH, HORN_PROGRAM, RULE_TYPE_ONTOLOGY

COMPARE_WORDS = {
    ">=": "以上",
    ">": "より大きい",
    "=<": "以下",
    "<": "未満",
    "=": "と等しい",
    "\\=": "と等しくない",
}

AGGREGATE_WORDS = {"sum": "合計", "max": "最大値", "min": "最小値"}

_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}
_PLACEHOLDER = re.compile(r"\{(\d+)\}")


def _variables_by_key() -> dict[str, dict[str, str]]:
    """述語キー → その述語を定義したノード（rule-type / term）の変数の呼び名。"""
    by_node: dict[str, dict[str, str]] = {}
    for node in GRAPH.nodes_of_kind("rule-type"):
        by_node[node.id] = dict(RULE_TYPE_ONTOLOGY[str(getattr(node, "rule_type"))].variables)
    for node in GRAPH.terms():
        if getattr(node, "horn_clause", None):
            by_node[node.id] = dict(getattr(node, "variables", None) or {})
    return {
        key: by_node[origin]
        for key, origin in HORN_PROGRAM.origins.items()
        if origin in by_node
    }


VARIABLES: dict[str, dict[str, str]] = _variables_by_key()

#: 述語キー → 読み下しの型（全 rule-type の readings を合わせたもの）
READINGS: dict[str, str] = {
    key: template
    for ontology in RULE_TYPE_ONTOLOGY.values()
    for key, template in ontology.readings.items()
}

#: 用語の CURIE → 読み下しの型（term ノードの reading）
TERM_READINGS: dict[str, str] = {
    str(getattr(node, "curie")): str(getattr(node, "reading"))
    for node in GRAPH.terms()
    if getattr(node, "reading", None)
}


def variables_for(key: str) -> dict[str, str]:
    """述語キーを定義したノードの変数の呼び名。"""
    return dict(VARIABLES.get(key, {}))


def _fill(template: str, names: list[str]) -> str:
    return _PLACEHOLDER.sub(lambda m: names[int(m.group(1)) - 1], template)


def _connect(text: str, tail: str) -> str:
    """「{text}、である{tail}」。text が「である」で終わるときは「、である」を省く。"""
    if text.endswith("である"):
        return text + tail
    return text + "、である" + tail


class _Namer:
    """引数の呼び名を決める。"""

    def __init__(self, variables: dict[str, str], graph: KnowledgeGraph | None):
        self.variables = variables
        self.graph = graph

    def name(self, term: Any) -> str:
        if isinstance(term, Var):
            base = format_term(term)
            return "〈" + self.variables.get(base, base) + "〉"
        if isinstance(term, str):
            if self.graph is not None and self.graph.has_node(term):
                name = self.graph.value(self.graph.node(term), "schema:name")
                if name is not None:
                    return str(name)
            if GRAPH.has_term(term):
                return GRAPH.term(term).label
        return format_constant(term)

    def expr(self, expr: Any) -> str:
        """式の形式表記（変数・定数だけ呼び名に置き換える）。"""
        if isinstance(expr, BinOp):
            return (
                self._operand(expr.left, expr, False)
                + " " + expr.op + " "
                + self._operand(expr.right, expr, True)
            )
        if isinstance(expr, Func):
            return expr.name + "(" + ", ".join(self.expr(a) for a in expr.args) + ")"
        return self.name(expr)

    def _operand(self, child: Any, parent: BinOp, right: bool) -> str:
        text = self.expr(child)
        if isinstance(child, BinOp):
            mine = _PRECEDENCE[child.op]
            theirs = _PRECEDENCE[parent.op]
            if mine < theirs or (right and mine == theirs):
                return "(" + text + ")"
        return text


def _read_atom(goal: Atom, namer: _Namer) -> str:
    names = [namer.name(arg) for arg in goal.args]
    template = READINGS.get(goal.key)
    if template is not None:
        return _fill(template, names)
    if goal.predicate == "rdf:type" and len(names) == 2:
        return f"{names[0]}は{names[1]}である"
    if len(names) == 2 and GRAPH.has_term(goal.predicate):
        reading = TERM_READINGS.get(goal.predicate)
        if reading is not None:
            return _fill(reading, names)
        return f"{names[0]}の{GRAPH.term(goal.predicate).label}は{names[1]}"
    if not names:
        return goal.predicate
    return goal.predicate + "(" + ", ".join(names) + ")"


def read_literal(literal: Any, namer: _Namer) -> str:
    if isinstance(literal, Atom):
        return _read_atom(literal, namer)
    if isinstance(literal, Compare):
        word = COMPARE_WORDS[literal.op]
        return f"{namer.expr(literal.left)}が{namer.expr(literal.right)}{word}"
    if isinstance(literal, Is):
        return f"{namer.expr(literal.target)}は {namer.expr(literal.expr)} で求める"
    if isinstance(literal, Aggregate):
        goal = read_literal(literal.goal, namer)
        result = namer.expr(literal.result)
        if literal.kind == "count":
            return _connect(goal, f"ようなものの件数は{result}")
        word = AGGREGATE_WORDS[literal.kind]
        return _connect(goal, f"ような{namer.expr(literal.template)}の{word}は{result}")
    if isinstance(literal, Forall):
        return _connect(
            read_literal(literal.condition, namer),
            f"ものすべてについて、{read_literal(literal.action, namer)}",
        )
    if isinstance(literal, Not):
        return f"{read_literal(literal.goal, namer)}、ということはない"
    if isinstance(literal, Conj):
        return "、かつ".join(read_literal(goal, namer) for goal in literal.goals)
    return namer.name(literal)


def read_clause(clause: Clause, graph: KnowledgeGraph | None = None) -> dict[str, Any]:
    """節を ``{"head": 頭部の読み, "conditions": [本体各リテラルの読み]}`` にする。

    画面では「{head} のは、次のすべてが成り立つときです」＋番号つき条件で示す。
    """
    namer = _Namer(variables_for(clause.head.key), graph)
    return {
        "head": read_literal(clause.head, namer),
        "conditions": [read_literal(literal, namer) for literal in clause.body],
    }


def read_rule_type(rule_type: str, graph: KnowledgeGraph | None = None) -> list[dict[str, Any]]:
    """rule-type が定義する節すべての読み下し（節の並びはグラフ文書のとおり）。"""
    origin = f"rule-type/{rule_type}"
    readings: list[dict[str, Any]] = []
    for clause in HORN_PROGRAM.clauses:
        if HORN_PROGRAM.origins.get(clause.head.key) == origin:
            readings.append({"source": clause.source, **read_clause(clause, graph)})
    return readings


# --- 証明木 ------------------------------------------------------------------


def _parse_goal(text: str, kind: str) -> Any:
    """証明木の goal（形式表記の文字列）をリテラルへ戻す。"""
    parser = _Parser(text, "")
    if kind == "builtin":
        # 代入後は is の左辺も定数になるので、式・演算子・式として読む
        left = parser.parse_expr()
        operator = parser.advance()
        right = parser.parse_expr()
        if operator.text == "is":
            return Is(left, right)
        return Compare(operator.text, left, right)
    return parser.parse_goal_arg()


def annotate(
    proof: dict[str, Any],
    graph: KnowledgeGraph | None = None,
    variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    """証明木（``ProofNode.to_dict()``）の各ノードに ``reading`` を付けた写しを返す。

    値が代入された goal を読むので、定数は schema:name で示される
    （例「山田太郎の学年は3」「3が4以上」）。未束縛の変数は、その節を定義した
    rule-type（または term ノード）の呼び名で〈〉に入れて示す。
    """
    try:
        literal = _parse_goal(str(proof["goal"]), str(proof["kind"]))
    except HornError:
        literal = None
    if variables is None:
        variables = variables_for(literal.key) if isinstance(literal, Atom) else {}
    namer = _Namer(variables, graph)
    reading = read_literal(literal, namer) if literal is not None else str(proof["goal"])
    inner = variables
    if proof["kind"] == "rule" and isinstance(literal, Atom) and literal.key in VARIABLES:
        inner = variables_for(literal.key)
    annotated = dict(proof)
    annotated["reading"] = reading
    annotated["children"] = [annotate(child, graph, inner) for child in proof["children"]]
    return annotated


def proof_goal_key(proof: dict[str, Any]) -> str | None:
    """証明木の根の述語キー（例 ``"eligible_to_register/2"``）。"""
    try:
        literal = _parse_goal(str(proof["goal"]), str(proof["kind"]))
    except HornError:
        return None
    return literal.key if isinstance(literal, Atom) else None


def read_predicate(key: str, graph: KnowledgeGraph | None = None) -> list[dict[str, Any]]:
    """述語キーを定義する節すべての読み下し。"""
    return [
        {"source": clause.source, **read_clause(clause, graph)}
        for clause in HORN_PROGRAM.clauses_for(key)
    ]
