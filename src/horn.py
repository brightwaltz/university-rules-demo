"""Horn 節インタープリタ（Datalog ＋ 層化否定 ＋ 集約）。

規程のルールを Prolog の部分集合で書き、ナレッジグラフ（JSON-LD）から
取り出した事実の上で実際に評価する。外部ライブラリは使わない。

- 変数に入るのはノードの識別子（CURIE 文字列）かリテラルだけ（Datalog）。
  関数記号（複合項）は無いので、グラフ同士の単一化は起こらない。
- 否定 ``\\+``・``forall``・``aggregate_all`` は層化されたプログラムでだけ使える。
- 探索は Prolog と同じ深さ優先・左から右・上から下。

後で ``docs/assets/horn.js`` へ一行ずつ移植するため、eval やメタプログラミングは
使わず、素直な手続き的コードにしている。形式表記（``format_term``）は JS 版と
完全に一致させること。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


class HornError(ValueError):
    """構文・安全性・層化・実行時の誤りを示す。"""


# --- 項とリテラル ------------------------------------------------------------


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class Atom:
    predicate: str
    args: tuple

    @property
    def key(self) -> str:
        return f"{self.predicate}/{len(self.args)}"


@dataclass(frozen=True)
class Compare:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Is:
    target: Var
    expr: Any


@dataclass(frozen=True)
class Not:
    goal: Any


@dataclass(frozen=True)
class Forall:
    condition: Any
    action: Any


@dataclass(frozen=True)
class Aggregate:
    kind: str  # sum / count / max / min
    template: Any  # count のときは None
    goal: Any
    result: Any


@dataclass(frozen=True)
class Conj:
    goals: tuple


@dataclass(frozen=True)
class BinOp:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Func:
    name: str
    args: tuple


@dataclass(frozen=True)
class Clause:
    head: Atom
    body: tuple
    source: str  # 原文の切り出し（頭部の先頭〜終端の '.' まで）


def atom(predicate: str, *args) -> Atom:
    """``Atom`` を作る便利関数。"""
    return Atom(predicate, tuple(args))


COMPARE_OPS = (">=", ">", "=<", "<", "=", "\\=")
ARITH_FUNCS = {"max": 2, "min": 2, "ceiling": 1, "floor": 1, "round": 1, "abs": 1}
AGGREGATE_KINDS = ("sum", "count", "max", "min")

NAME_PATTERN = r"[a-z][A-Za-z0-9_]*(?::[A-Za-z0-9_][A-Za-z0-9_/\-]*)?"
NAME_RE = re.compile(NAME_PATTERN)

# 改名した変数の接尾辞の区切り。変数名には現れない文字を使う
RENAME_MARK = "#"
ANONYMOUS_PREFIX = "_" + RENAME_MARK


# --- 形式表記（JS 版と完全一致させる） ---------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def format_constant(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        if value.is_integer():
            return str(int(value))
        # JS の Math.round(x*1e6)/1e6 と同じ丸め（0.5 は正の方向へ）
        rounded = math.floor(value * 1e6 + 0.5) / 1e6
        if rounded.is_integer():
            return str(int(rounded))
        return repr(rounded)
    text = str(value)
    if NAME_RE.fullmatch(text):
        return text
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _walk(term: Any, subst: dict[str, Any]) -> Any:
    """変数をたどって束縛先へ置き換える（未束縛ならその変数）。"""
    while isinstance(term, Var) and term.name in subst:
        term = subst[term.name]
    return term


_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


def _format_operand(child: Any, parent: BinOp, right: bool, subst: dict[str, Any]) -> str:
    """二項演算の子。優先順位が親より低いか、右の子で同じ優先順位なら括弧で囲む（左結合）。"""
    text = _format_expr(child, subst)
    if isinstance(child, BinOp):
        mine = _PRECEDENCE[child.op]
        theirs = _PRECEDENCE[parent.op]
        if mine < theirs or (right and mine == theirs):
            return "(" + text + ")"
    return text


def _format_expr(expr: Any, subst: dict[str, Any]) -> str:
    if isinstance(expr, BinOp):
        return (
            _format_operand(expr.left, expr, False, subst)
            + " " + expr.op + " "
            + _format_operand(expr.right, expr, True, subst)
        )
    if isinstance(expr, Func):
        return expr.name + "(" + ", ".join(_format_expr(a, subst) for a in expr.args) + ")"
    return format_term(expr, subst)


def format_term(term: Any, subst: dict[str, Any] | None = None) -> str:
    """項・式・リテラルを形式表記にする。``subst`` があれば束縛を代入する。"""
    if subst is None:
        subst = {}
    if isinstance(term, Var):
        value = _walk(term, subst)
        if isinstance(value, Var):
            return value.name.split(RENAME_MARK)[0]
        return format_constant(value)
    if isinstance(term, Atom):
        if not term.args:
            return term.predicate
        return term.predicate + "(" + ", ".join(format_term(a, subst) for a in term.args) + ")"
    if isinstance(term, Compare):
        return (
            _format_expr(term.left, subst) + " " + term.op + " "
            + _format_expr(term.right, subst)
        )
    if isinstance(term, Is):
        return format_term(term.target, subst) + " is " + _format_expr(term.expr, subst)
    if isinstance(term, (BinOp, Func)):
        return _format_expr(term, subst)
    if isinstance(term, Not):
        return "\\+ " + format_term(term.goal, subst)
    if isinstance(term, Forall):
        return (
            "forall(" + format_term(term.condition, subst) + ", "
            + format_term(term.action, subst) + ")"
        )
    if isinstance(term, Aggregate):
        if term.kind == "count":
            spec = "count"
        else:
            spec = term.kind + "(" + _format_expr(term.template, subst) + ")"
        return (
            "aggregate_all(" + spec + ", " + format_term(term.goal, subst) + ", "
            + format_term(term.result, subst) + ")"
        )
    if isinstance(term, Conj):
        return "(" + ", ".join(format_term(g, subst) for g in term.goals) + ")"
    return format_constant(term)


# --- 字句解析 ----------------------------------------------------------------

_TOKEN_SPECS = [
    ("number", re.compile(r"\d+(?:\.\d+)?")),
    ("var", re.compile(r"[A-Z_][A-Za-z0-9_]*")),
    ("name", NAME_RE),
    ("punct", re.compile(r":-|\\\+|\\=|>=|=<|[><=(),.+\-*/]")),
]


@dataclass
class _Token:
    kind: str  # number / var / name / string / punct / end
    text: str
    value: Any
    line: int
    start: int
    end: int


def _tokenize(text: str, origin: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    line = 1
    while pos < len(text):
        char = text[pos]
        if char == "\n":
            line += 1
            pos += 1
            continue
        if char in " \t\r":
            pos += 1
            continue
        if char == "%":
            while pos < len(text) and text[pos] != "\n":
                pos += 1
            continue
        if char in "'\"":
            # 文字列。エスケープは \\ \' \" のみ
            quote = char
            start = pos
            pos += 1
            chars: list[str] = []
            while True:
                if pos >= len(text) or text[pos] == "\n":
                    raise _syntax_error(origin, line, "文字列が閉じていません")
                current = text[pos]
                if current == "\\":
                    following = text[pos + 1] if pos + 1 < len(text) else ""
                    if following not in ("\\", "'", '"'):
                        raise _syntax_error(origin, line, f"使えないエスケープです: \\{following}")
                    chars.append(following)
                    pos += 2
                    continue
                if current == quote:
                    pos += 1
                    break
                chars.append(current)
                pos += 1
            tokens.append(_Token("string", text[start:pos], "".join(chars), line, start, pos))
            continue
        matched = False
        for kind, pattern in _TOKEN_SPECS:
            match = pattern.match(text, pos)
            if match:
                word = match.group(0)
                value: Any = word
                if kind == "number":
                    value = float(word) if "." in word else int(word)
                tokens.append(_Token(kind, word, value, line, pos, match.end()))
                pos = match.end()
                matched = True
                break
        if not matched:
            raise _syntax_error(origin, line, f"解釈できない文字があります: {char!r}")
    tokens.append(_Token("end", "", None, line, len(text), len(text)))
    return tokens


def _syntax_error(origin: str, line: int, message: str) -> HornError:
    prefix = f"{origin} " if origin else ""
    return HornError(f"{prefix}{line}行目: 構文エラー: {message}")


# --- 構文解析 ----------------------------------------------------------------


class _Parser:
    def __init__(self, text: str, origin: str):
        self.text = text
        self.origin = origin
        self.tokens = _tokenize(text, origin)
        self.index = 0
        self.anonymous_count = 0

    # 低レベル
    def peek(self, offset: int = 0) -> _Token:
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def advance(self) -> _Token:
        token = self.tokens[self.index]
        if token.kind != "end":
            self.index += 1
        return token

    def at(self, text: str) -> bool:
        token = self.peek()
        return token.kind == "punct" and token.text == text

    def expect(self, text: str) -> _Token:
        token = self.peek()
        if token.kind != "punct" or token.text != text:
            raise self.error(f"'{text}' が必要です（{self.describe(token)} があります）")
        return self.advance()

    def describe(self, token: _Token) -> str:
        if token.kind == "end":
            return "入力の終わり"
        return f"'{token.text}'"

    def error(self, message: str, token: _Token | None = None) -> HornError:
        if token is None:
            token = self.peek()
        return _syntax_error(self.origin, token.line, message)

    # 節
    def parse_program(self) -> list[Clause]:
        clauses: list[Clause] = []
        while self.peek().kind != "end":
            clauses.append(self.parse_clause())
        return clauses

    def parse_clause(self) -> Clause:
        first = self.peek()
        if first.kind != "name":
            raise self.error(f"節の頭部は述語で始めます（{self.describe(first)} があります）")
        head = self.parse_atom()
        body: list[Any] = []
        if self.at(":-"):
            self.advance()
            body = self.parse_literals()
        last = self.expect(".")
        return Clause(head, tuple(body), self.text[first.start:last.end])

    def parse_literals(self) -> list[Any]:
        literals = [self.parse_literal()]
        while self.at(","):
            self.advance()
            literals.append(self.parse_literal())
        return literals

    def parse_literal(self) -> Any:
        token = self.peek()
        if self.at("\\+"):
            self.advance()
            return Not(self.parse_goal_arg())
        if token.kind == "name":
            following = self.peek(1)
            opens = following.kind == "punct" and following.text == "("
            if opens and token.text == "not":
                self.advance()
                self.expect("(")
                goal = self.parse_goal_arg()
                self.expect(")")
                return Not(goal)
            if opens and token.text == "forall":
                self.advance()
                self.expect("(")
                condition = self.parse_goal_arg()
                self.expect(",")
                action = self.parse_goal_arg()
                self.expect(")")
                return Forall(condition, action)
            if opens and token.text == "aggregate_all":
                return self.parse_aggregate()
            if token.text not in ARITH_FUNCS:
                result = self.parse_atom()
                after = self.peek()
                if (after.kind == "punct" and after.text in COMPARE_OPS) or (
                    after.kind == "name" and after.text == "is"
                ):
                    raise self.error("アトムの後に比較演算子や is は書けません", after)
                return result
        left = self.parse_expr()
        operator = self.peek()
        if operator.kind == "name" and operator.text == "is":
            self.advance()
            if not isinstance(left, Var):
                raise self.error("is の左辺は変数でなければなりません", operator)
            return Is(left, self.parse_expr())
        if operator.kind == "punct" and operator.text in COMPARE_OPS:
            self.advance()
            return Compare(operator.text, left, self.parse_expr())
        raise self.error(f"比較演算子か is が必要です（{self.describe(operator)} があります）")

    def parse_goal_arg(self) -> Any:
        """否定・forall・集約の中のゴール。アトムか括弧つき連言。"""
        if self.at("("):
            self.advance()
            literals = self.parse_literals()
            self.expect(")")
            if len(literals) == 1:
                return literals[0]
            return Conj(tuple(literals))
        return self.parse_literal()

    def parse_aggregate(self) -> Aggregate:
        self.advance()  # aggregate_all
        self.expect("(")
        spec = self.peek()
        if spec.kind != "name" or spec.text not in AGGREGATE_KINDS:
            raise self.error("aggregate_all の第1引数は sum(T) / count / max(T) / min(T) です")
        self.advance()
        template = None
        if spec.text != "count":
            self.expect("(")
            template = self.parse_expr()
            self.expect(")")
        self.expect(",")
        goal = self.parse_goal_arg()
        self.expect(",")
        result = self.parse_term()
        self.expect(")")
        return Aggregate(spec.text, template, goal, result)

    def parse_atom(self) -> Atom:
        token = self.advance()
        if token.kind != "name":
            raise self.error(f"述語名が必要です（{self.describe(token)} があります）", token)
        args: list[Any] = []
        if self.at("("):
            self.advance()
            args.append(self.parse_term())
            while self.at(","):
                self.advance()
                args.append(self.parse_term())
            self.expect(")")
        return Atom(token.text, tuple(args))

    def parse_term(self) -> Any:
        token = self.peek()
        if token.kind == "var":
            self.advance()
            if token.text == "_":
                # 無名変数は出現ごとに別物
                self.anonymous_count += 1
                return Var(f"{ANONYMOUS_PREFIX}{self.anonymous_count}")
            return Var(token.text)
        if token.kind in ("number", "string", "name"):
            self.advance()
            if token.kind == "name" and self.at("("):
                raise self.error(f"引数に複合項は書けません: {token.text}(...)")
            return token.value
        raise self.error(f"項が必要です（{self.describe(token)} があります）")

    # 算術式
    def parse_expr(self) -> Any:
        left = self.parse_product()
        while self.at("+") or self.at("-"):
            op = self.advance().text
            left = BinOp(op, left, self.parse_product())
        return left

    def parse_product(self) -> Any:
        left = self.parse_factor()
        while self.at("*") or self.at("/"):
            op = self.advance().text
            left = BinOp(op, left, self.parse_factor())
        return left

    def parse_factor(self) -> Any:
        token = self.peek()
        if self.at("("):
            self.advance()
            inner = self.parse_expr()
            self.expect(")")
            return inner
        if token.kind == "name" and self.peek(1).kind == "punct" and self.peek(1).text == "(":
            if token.text not in ARITH_FUNCS:
                raise self.error(f"算術式に使えない関数です: {token.text}")
            self.advance()
            self.expect("(")
            args = [self.parse_expr()]
            while self.at(","):
                self.advance()
                args.append(self.parse_expr())
            self.expect(")")
            if len(args) != ARITH_FUNCS[token.text]:
                raise self.error(
                    f"{token.text} の引数は {ARITH_FUNCS[token.text]} 個です", token
                )
            return Func(token.text, tuple(args))
        return self.parse_term()


# --- 変数の収集と改名 --------------------------------------------------------


def _collect_vars(term: Any, out: list[str]) -> None:
    """項・リテラルに現れる変数名を出現順に集める（重複なし）。"""
    if isinstance(term, Var):
        if term.name not in out:
            out.append(term.name)
    elif isinstance(term, Atom):
        for arg in term.args:
            _collect_vars(arg, out)
    elif isinstance(term, (Compare, BinOp)):
        _collect_vars(term.left, out)
        _collect_vars(term.right, out)
    elif isinstance(term, Is):
        _collect_vars(term.target, out)
        _collect_vars(term.expr, out)
    elif isinstance(term, Func):
        for arg in term.args:
            _collect_vars(arg, out)
    elif isinstance(term, Not):
        _collect_vars(term.goal, out)
    elif isinstance(term, Forall):
        _collect_vars(term.condition, out)
        _collect_vars(term.action, out)
    elif isinstance(term, Aggregate):
        if term.template is not None:
            _collect_vars(term.template, out)
        _collect_vars(term.goal, out)
        _collect_vars(term.result, out)
    elif isinstance(term, Conj):
        for goal in term.goals:
            _collect_vars(goal, out)


def _rename(term: Any, suffix: str) -> Any:
    """節を呼ぶたびに変数を新しい名前へ付け替える。"""
    if isinstance(term, Var):
        return Var(term.name + suffix)
    if isinstance(term, Atom):
        return Atom(term.predicate, tuple(_rename(a, suffix) for a in term.args))
    if isinstance(term, Compare):
        return Compare(term.op, _rename(term.left, suffix), _rename(term.right, suffix))
    if isinstance(term, BinOp):
        return BinOp(term.op, _rename(term.left, suffix), _rename(term.right, suffix))
    if isinstance(term, Is):
        return Is(_rename(term.target, suffix), _rename(term.expr, suffix))
    if isinstance(term, Func):
        return Func(term.name, tuple(_rename(a, suffix) for a in term.args))
    if isinstance(term, Not):
        return Not(_rename(term.goal, suffix))
    if isinstance(term, Forall):
        return Forall(_rename(term.condition, suffix), _rename(term.action, suffix))
    if isinstance(term, Aggregate):
        template = None if term.template is None else _rename(term.template, suffix)
        return Aggregate(
            term.kind, template, _rename(term.goal, suffix), _rename(term.result, suffix)
        )
    if isinstance(term, Conj):
        return Conj(tuple(_rename(g, suffix) for g in term.goals))
    return term


def _atoms_in(term: Any, negative: bool, out: list[tuple[Atom, bool]]) -> None:
    """リテラル中のアトムを (アトム, 否定的か) で集める。"""
    if isinstance(term, Atom):
        out.append((term, negative))
    elif isinstance(term, Not):
        _atoms_in(term.goal, True, out)
    elif isinstance(term, Forall):
        _atoms_in(term.condition, True, out)
        _atoms_in(term.action, True, out)
    elif isinstance(term, Aggregate):
        _atoms_in(term.goal, True, out)
    elif isinstance(term, Conj):
        for goal in term.goals:
            _atoms_in(goal, negative, out)


def _binding_vars(literal: Any, out: list[str]) -> None:
    """安全性検査で「頭部の変数を束縛できる」とみなす位置の変数を集める。

    本体のトップレベルの肯定アトム・is の左辺・集約の結果・= の両辺だけを数える。
    否定・forall の内側と集約のゴールの中は数えない（そこでの束縛は外へ出ないため）。
    """
    if isinstance(literal, Atom):
        _collect_vars(literal, out)
    elif isinstance(literal, Is):
        _collect_vars(literal.target, out)
    elif isinstance(literal, Compare):
        if literal.op == "=":
            _collect_vars(literal.left, out)
            _collect_vars(literal.right, out)
    elif isinstance(literal, Aggregate):
        _collect_vars(literal.result, out)


def _one_line(source: str) -> str:
    return " ".join(source.split())


# --- プログラム --------------------------------------------------------------


class Program:
    """節の集まり。述語キー（``pred/arity``）で索引する。"""

    def __init__(self, clauses: Iterable[Clause] = (), origins: dict[str, str] | None = None):
        self.clauses: list[Clause] = list(clauses)
        # 述語キー → その述語を定義した origin（combine の重複検査に使う）
        self.origins: dict[str, str] = dict(origins or {})
        self._by_key: dict[str, list[Clause]] = {}
        for clause in self.clauses:
            self._by_key.setdefault(clause.head.key, []).append(clause)

    @classmethod
    def parse(cls, text: str, origin: str = "") -> Program:
        clauses = _Parser(text, origin).parse_program()
        origins = {clause.head.key: origin for clause in clauses}
        return cls(clauses, origins)

    @classmethod
    def combine(cls, programs: list[Program]) -> Program:
        clauses: list[Clause] = []
        origins: dict[str, str] = {}
        for program in programs:
            for key in program.defined():
                origin = program.origins.get(key, "")
                if key in origins and origins[key] != origin:
                    raise HornError(
                        f"述語 {key} が {origins[key]} と {origin} の2箇所で定義されています"
                        "（1つの述語は1箇所で定義してください）"
                    )
                origins[key] = origin
            clauses.extend(program.clauses)
        return cls(clauses, origins)

    def defined(self) -> set[str]:
        return set(self._by_key)

    def clauses_for(self, key: str) -> list[Clause]:
        return list(self._by_key.get(key, []))

    def dependencies(self, key: str) -> list[tuple[str, bool]]:
        result: list[tuple[str, bool]] = []
        for clause in self._by_key.get(key, []):
            found: list[tuple[Atom, bool]] = []
            for literal in clause.body:
                _atoms_in(literal, False, found)
            for body_atom, negative in found:
                edge = (body_atom.key, negative)
                if edge not in result:
                    result.append(edge)
        return result

    def _reaches(self, start: str, target: str) -> bool:
        """依存グラフで start から target へ（肯定・否定を問わず）たどれるか。"""
        seen: list[str] = []
        stack = [start]
        while stack:
            key = stack.pop()
            if key == target:
                return True
            if key in seen:
                continue
            seen.append(key)
            for dependency, _negative in self.dependencies(key):
                stack.append(dependency)
        return False

    def strata(self) -> dict[str, int]:
        keys = sorted(self.defined())
        # 否定的な辺が循環に含まれていないかを先に調べる
        for key in keys:
            for dependency, negative in self.dependencies(key):
                if negative and self._reaches(dependency, key):
                    raise HornError(
                        f"層化できません: {key} が否定・forall・集約を通して {dependency} に依存し、"
                        f"{dependency} から {key} へ戻る循環があります"
                    )
        # 層番号を反復で更新する（肯定の辺は同じ層以上、否定的な辺は1つ上の層）
        levels: dict[str, int] = {key: 0 for key in keys}
        changed = True
        while changed:
            changed = False
            for key in keys:
                for dependency, negative in self.dependencies(key):
                    below = levels.get(dependency, 0)
                    needed = below + 1 if negative else below
                    if levels[key] < needed:
                        levels[key] = needed
                        changed = True
        return levels

    def listing(self, goal_keys: list[str]) -> str:
        sources: list[str] = []
        visited: list[str] = []

        def visit(key: str) -> None:
            if key in visited:
                return
            visited.append(key)
            for clause in self._by_key.get(key, []):
                sources.append(clause.source)
            for dependency, _negative in self.dependencies(key):
                visit(dependency)

        for key in goal_keys:
            visit(key)
        return "\n".join(sources)

    def check(self) -> None:
        for clause in self.clauses:
            head_vars: list[str] = []
            _collect_vars(clause.head, head_vars)
            bound: list[str] = []
            for literal in clause.body:
                _binding_vars(literal, bound)
            unsafe = [
                name.split(RENAME_MARK)[0] for name in head_vars if name not in bound
            ]
            if unsafe:
                raise HornError(
                    f"安全でない節です（頭部の変数 {', '.join(unsafe)} が本体で束縛されません）: "
                    f"{_one_line(clause.source)}"
                )
        self.strata()


# --- 事実 --------------------------------------------------------------------


def _index_key(value: Any) -> tuple[str, Any]:
    """事実の索引・重複判定に使うキー（true と 1 を区別する）。"""
    if isinstance(value, bool):
        return ("b", value)
    if _is_number(value):
        return ("n", value)
    return ("s", value)


class FactBase:
    """述語ごとの事実の集まり。同じ事実は1回だけ持つ（RDF のトリプルは集合）。"""

    def __init__(self, facts: Iterable[tuple[str, tuple]] = ()):
        self._facts: dict[str, list[tuple]] = {}
        self._by_first: dict[str, dict[tuple[str, Any], list[tuple]]] = {}
        self._seen: set[tuple] = set()
        for predicate, args in facts:
            self.add(predicate, args)

    @classmethod
    def from_jsonld(cls, nodes: Iterable[dict]) -> FactBase:
        base = cls()
        for node in nodes:
            subject = node.get("@id")
            if subject is None:
                continue
            types = node.get("@type", [])
            if not isinstance(types, list):
                types = [types]
            for type_name in types:
                base.add("rdf:type", (subject, type_name))
            for key, raw in node.items():
                if key.startswith("@"):
                    continue
                values = raw if isinstance(raw, list) else [raw]
                for value in values:
                    if isinstance(value, dict):
                        if "@id" in value:
                            base.add(key, (subject, value["@id"]))
                        elif "@value" in value:
                            base.add(key, (subject, value["@value"]))
                        # それ以外（入れ子の無名ノード等）は無視する
                    elif isinstance(value, (str, int, float, bool)):
                        base.add(key, (subject, value))
        return base

    def add(self, predicate: str, args: tuple) -> None:
        args = tuple(args)
        key = f"{predicate}/{len(args)}"
        identity = (key,) + tuple(_index_key(a) for a in args)
        if identity in self._seen:
            return
        self._seen.add(identity)
        self._facts.setdefault(key, []).append(args)
        if args:
            index = self._by_first.setdefault(key, {})
            index.setdefault(_index_key(args[0]), []).append(args)

    def match(self, predicate: str, arity: int, first: Any = None) -> list[tuple]:
        """述語の事実を返す。``first`` を渡すと第1引数がそれに一致するものだけ。"""
        key = f"{predicate}/{arity}"
        if first is None or arity == 0:
            return list(self._facts.get(key, []))
        return list(self._by_first.get(key, {}).get(_index_key(first), []))


# --- 証明木 ------------------------------------------------------------------


@dataclass
class ProofNode:
    goal: str  # 解の束縛を代入した形式表記
    kind: str  # rule / fact / builtin / not / forall / aggregate / conj
    proved: bool
    clause: str | None = None  # kind="rule" のとき使った節（1行）
    detail: str | None = None
    children: list[ProofNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "kind": self.kind,
            "proved": self.proved,
            "clause": self.clause,
            "detail": self.detail,
            "children": [child.to_dict() for child in self.children],
        }


# --- 単一化と算術 ------------------------------------------------------------


def _same_constant(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def _unify(a: Any, b: Any, subst: dict[str, Any]) -> dict[str, Any] | None:
    """単一化。成功すれば新しい束縛（元の辞書は変えない）、失敗なら None。"""
    a = _walk(a, subst)
    b = _walk(b, subst)
    if isinstance(a, Var) and isinstance(b, Var) and a.name == b.name:
        return subst
    if isinstance(a, Var):
        extended = dict(subst)
        extended[a.name] = b
        return extended
    if isinstance(b, Var):
        extended = dict(subst)
        extended[b.name] = a
        return extended
    return subst if _same_constant(a, b) else None


def _unify_args(left: tuple, right: tuple, subst: dict[str, Any]) -> dict[str, Any] | None:
    if len(left) != len(right):
        return None
    current: dict[str, Any] | None = subst
    for a, b in zip(left, right):
        current = _unify(a, b, current)
        if current is None:
            return None
    return current


def _normalize(value: Any) -> Any:
    """整数値の float を int にする（JS と表記を揃えるため）。"""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _is_ground(term: Any, subst: dict[str, Any], allow_anonymous: bool = False) -> bool:
    names: list[str] = []
    _collect_vars(term, names)
    for name in names:
        if allow_anonymous and name.startswith(ANONYMOUS_PREFIX):
            continue  # 否定の内側の無名変数は、その場限りの存在量化なので許す
        if isinstance(_walk(Var(name), subst), Var):
            return False
    return True


def _evaluate(expr: Any, subst: dict[str, Any], context: Any) -> Any:
    """算術式を評価する。素の項は値をそのまま返す（文字列の比較に使う）。"""
    if isinstance(expr, Var):
        value = _walk(expr, subst)
        if isinstance(value, Var):
            raise HornError(f"式に未束縛の変数があります: {format_term(context, subst)}")
        return value
    if isinstance(expr, BinOp):
        left = _number(_evaluate(expr.left, subst, context), context, subst)
        right = _number(_evaluate(expr.right, subst, context), context, subst)
        if expr.op == "+":
            return left + right
        if expr.op == "-":
            return left - right
        if expr.op == "*":
            return left * right
        if right == 0:
            raise HornError(f"0 で割りました: {format_term(context, subst)}")
        return left / right
    if isinstance(expr, Func):
        values = [_number(_evaluate(a, subst, context), context, subst) for a in expr.args]
        if expr.name == "max":
            return values[0] if values[0] >= values[1] else values[1]
        if expr.name == "min":
            return values[0] if values[0] <= values[1] else values[1]
        if expr.name == "ceiling":
            return math.ceil(values[0])
        if expr.name == "floor":
            return math.floor(values[0])
        if expr.name == "round":
            # JS の Math.round と同じ（0.5 は正の方向へ）
            return math.floor(values[0] + 0.5)
        return abs(values[0])
    return expr


def _number(value: Any, context: Any, subst: dict[str, Any]) -> Any:
    if not _is_number(value):
        raise HornError(
            f"数値でない値を算術に使っています（{format_constant(value)}）: "
            f"{format_term(context, subst)}"
        )
    return value


# --- 評価器 ------------------------------------------------------------------


class Solver:
    """プログラムと事実の上で問い合わせを解く。"""

    def __init__(
        self,
        program: Program,
        facts: FactBase,
        max_depth: int = 64,
        max_steps: int = 200_000,
    ):
        self.program = program
        self.facts = facts
        self.max_depth = max_depth
        self.max_steps = max_steps
        self._defined = program.defined()
        self._steps = 0
        self._fresh = 0

    # 公開 API
    def solve(self, goal: Any) -> Iterator[dict[str, Any]]:
        self._start()
        names: list[str] = []
        _collect_vars(goal, names)
        for subst, _node in self._solve(goal, {}, 0):
            answer: dict[str, Any] = {}
            for name in names:
                if name.startswith(ANONYMOUS_PREFIX):
                    continue
                value = _walk(Var(name), subst)
                if not isinstance(value, Var):
                    answer[name] = value
            yield answer

    def holds(self, goal: Any) -> bool:
        for _answer in self.solve(goal):
            return True
        return False

    def value(self, goal: Any, variable: str) -> Any | None:
        for answer in self.solve(goal):
            return answer.get(variable)
        return None

    def explain(self, goal: Any) -> ProofNode:
        self._start()
        for _subst, node in self._solve(goal, {}, 0):
            return node
        return self._explain_failure(goal, {}, 0)

    # 内部
    def _start(self) -> None:
        self._steps = 0

    def _tick(self) -> None:
        self._steps += 1
        if self._steps > self.max_steps:
            raise HornError(f"手順数の上限（{self.max_steps}）を超えました")

    def _solve(self, goal: Any, subst: dict[str, Any], depth: int) -> Iterator[tuple[dict[str, Any], ProofNode]]:
        """ゴールの解を (束縛, 証明) の組で順に返す。"""
        self._tick()
        if isinstance(goal, Atom):
            yield from self._solve_atom(goal, subst, depth)
        elif isinstance(goal, Conj):
            for result, children in self._solve_body(goal.goals, 0, subst, depth):
                yield result, ProofNode(format_term(goal, result), "conj", True, None, None, children)
        elif isinstance(goal, Compare):
            result = self._compare(goal, subst)
            if result is not None:
                yield result, ProofNode(format_term(goal, result), "builtin", True)
        elif isinstance(goal, Is):
            value = _normalize(_evaluate(goal.expr, subst, goal))
            result = _unify(goal.target, value, subst)
            if result is not None:
                yield result, ProofNode(format_term(goal, result), "builtin", True)
        elif isinstance(goal, Not):
            self._check_ground(goal, subst)
            for _result, _node in self._solve(goal.goal, subst, depth):
                return
            yield subst, ProofNode(format_term(goal, subst), "not", True)
        elif isinstance(goal, Forall):
            children = self._forall_children(goal, subst, depth)
            if children is not None:
                yield subst, ProofNode(format_term(goal, subst), "forall", True, None, None, children)
        elif isinstance(goal, Aggregate):
            value, children = self._aggregate(goal, subst, depth)
            if value is not None:
                result = _unify(goal.result, value, subst)
                if result is not None:
                    yield result, ProofNode(
                        format_term(goal, result), "aggregate", True, None,
                        f"{len(children)}件", children,
                    )
        else:
            raise HornError(f"ゴールとして評価できません: {format_term(goal, subst)}")

    def _solve_atom(self, goal: Atom, subst: dict[str, Any], depth: int) -> Iterator[tuple[dict[str, Any], ProofNode]]:
        # 事実を先に、節を後に返す（同じ述語に両方あれば和）
        first = _walk(goal.args[0], subst) if goal.args else None
        if isinstance(first, Var):
            first = None
        for fact in self.facts.match(goal.predicate, len(goal.args), first):
            result = _unify_args(goal.args, fact, subst)
            if result is not None:
                yield result, ProofNode(format_term(goal, result), "fact", True)
        if goal.key not in self._defined:
            return
        if depth >= self.max_depth:
            raise HornError(
                f"深さの上限（{self.max_depth}）を超えました: {format_term(goal, subst)}"
            )
        for clause in self.program.clauses_for(goal.key):
            head, body = self._renamed(clause)
            start = _unify_args(goal.args, head.args, subst)
            if start is None:
                continue
            for result, children in self._solve_body(body, 0, start, depth + 1):
                yield result, ProofNode(
                    format_term(goal, result), "rule", True, _one_line(clause.source), None, children
                )

    def _solve_body(self, body: tuple, index: int, subst: dict[str, Any], depth: int) -> Iterator[tuple[dict[str, Any], list[ProofNode]]]:
        """本体のリテラルを左から順に解き、(束縛, 各リテラルの証明) を返す。"""
        if index == len(body):
            yield subst, []
            return
        for first, node in self._solve(body[index], subst, depth):
            for result, rest in self._solve_body(body, index + 1, first, depth):
                yield result, [node] + rest

    def _renamed(self, clause: Clause) -> tuple[Atom, tuple]:
        self._fresh += 1
        suffix = f"{RENAME_MARK}{self._fresh}"
        head = _rename(clause.head, suffix)
        body = tuple(_rename(literal, suffix) for literal in clause.body)
        return head, body

    def _compare(self, goal: Compare, subst: dict[str, Any]) -> dict[str, Any] | None:
        if goal.op == "=":
            return _unify(goal.left, goal.right, subst)
        if goal.op == "\\=":
            return subst if _unify(goal.left, goal.right, subst) is None else None
        if not (_is_ground(goal.left, subst) and _is_ground(goal.right, subst)):
            raise HornError(f"比較の両辺が束縛されていません: {format_term(goal, subst)}")
        left = _evaluate(goal.left, subst, goal)
        right = _evaluate(goal.right, subst, goal)
        both_numbers = _is_number(left) and _is_number(right)
        both_strings = isinstance(left, str) and isinstance(right, str)
        if not (both_numbers or both_strings):
            raise HornError(
                f"数値同士か文字列同士でなければ比較できません: {format_term(goal, subst)}"
            )
        if goal.op == ">=":
            ok = left >= right
        elif goal.op == ">":
            ok = left > right
        elif goal.op == "=<":
            ok = left <= right
        else:
            ok = left < right
        return subst if ok else None

    def _check_ground(self, goal: Not, subst: dict[str, Any]) -> None:
        if not _is_ground(goal.goal, subst, True):
            raise HornError(f"否定の対象に未束縛の変数があります: {format_term(goal, subst)}")

    def _forall_children(self, goal: Forall, subst: dict[str, Any], depth: int) -> list[ProofNode] | None:
        """C の各解について A の証明を集める。A に解の無い C の解があれば None。"""
        children: list[ProofNode] = []
        for condition_subst, _node in self._solve(goal.condition, subst, depth):
            proof = None
            for _result, node in self._solve(goal.action, condition_subst, depth):
                proof = node
                break
            if proof is None:
                return None
            children.append(proof)
        return children

    def _aggregate(self, goal: Aggregate, subst: dict[str, Any], depth: int) -> tuple[Any, list[ProofNode]]:
        """G の全解を集めて集約値を返す（重複も数える）。max/min が空なら値は None。"""
        children: list[ProofNode] = []
        values: list[Any] = []
        for result, node in self._solve(goal.goal, subst, depth):
            children.append(node)
            if goal.kind != "count":
                if not _is_ground(goal.template, result):
                    raise HornError(f"集約する値が束縛されていません: {format_term(goal, result)}")
                values.append(_number(_evaluate(goal.template, result, goal), goal, result))
        if goal.kind == "count":
            return len(children), children
        if goal.kind == "sum":
            total: Any = 0
            for value in values:
                total = total + value
            return _normalize(total), children
        if not values:
            return None, children
        best = values[0]
        for value in values[1:]:
            if (goal.kind == "max" and value > best) or (goal.kind == "min" and value < best):
                best = value
        return _normalize(best), children

    # 失敗の説明
    def _first_proof(self, goal: Any, subst: dict[str, Any], depth: int) -> tuple[dict[str, Any], ProofNode] | None:
        for result, node in self._solve(goal, subst, depth):
            return result, node
        return None

    def _longest_prefix(self, body: tuple, subst: dict[str, Any], depth: int) -> tuple[int, list[ProofNode], dict[str, Any]]:
        """解をもつ最長の接頭辞 body[:i] を探し、(i, 接頭辞の証明, その束縛) を返す。"""
        for length in range(len(body) - 1, 0, -1):
            for result, children in self._solve_body(body[:length], 0, subst, depth):
                return length, children, result
        return 0, [], subst

    def _explain_failure(self, goal: Any, subst: dict[str, Any], depth: int) -> ProofNode:
        """解の無いゴールについて、どこで行き詰まったかを木にする。"""
        text = format_term(goal, subst)
        if isinstance(goal, Atom):
            if goal.key not in self._defined:
                return ProofNode(text, "fact", False, None, "事実がない")
            best: tuple[int, list[ProofNode], dict[str, Any], tuple, Clause] | None = None
            for clause in self.program.clauses_for(goal.key):
                head, body = self._renamed(clause)
                start = _unify_args(goal.args, head.args, subst)
                if start is None:
                    continue
                length, children, reached = self._longest_prefix(body, start, depth + 1)
                if best is None or length > best[0]:
                    best = (length, children, reached, body, clause)
            if best is None:
                return ProofNode(text, "rule", False, None, "該当する節がない")
            length, children, reached, body, clause = best
            children = children + [self._explain_failure(body[length], reached, depth + 1)]
            return ProofNode(text, "rule", False, _one_line(clause.source), None, children)
        if isinstance(goal, Conj):
            length, children, reached = self._longest_prefix(goal.goals, subst, depth)
            children = children + [self._explain_failure(goal.goals[length], reached, depth)]
            return ProofNode(text, "conj", False, None, None, children)
        if isinstance(goal, (Compare, Is)):
            return ProofNode(text, "builtin", False)
        if isinstance(goal, Not):
            self._check_ground(goal, subst)
            found = self._first_proof(goal.goal, subst, depth)
            children = [] if found is None else [found[1]]
            return ProofNode(text, "not", False, None, None, children)
        if isinstance(goal, Forall):
            children: list[ProofNode] = []
            for condition_subst, _node in self._solve(goal.condition, subst, depth):
                found = self._first_proof(goal.action, condition_subst, depth)
                if found is None:
                    children.append(self._explain_failure(goal.action, condition_subst, depth))
                else:
                    children.append(found[1])
            return ProofNode(text, "forall", False, None, None, children)
        if isinstance(goal, Aggregate):
            _value, children = self._aggregate(goal, subst, depth)
            return ProofNode(text, "aggregate", False, None, f"{len(children)}件", children)
        raise HornError(f"ゴールとして評価できません: {text}")
