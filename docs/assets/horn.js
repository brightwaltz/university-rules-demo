// Horn 節インタープリタ（Datalog ＋ 層化否定 ＋ 集約）と、その読み下し。
//
// src/horn.py と src/horn_reading.py を一行ずつ移植したもの。形式表記・証明木・
// 読み下し・エラーの文言は Python 版と一字一句同じにする（tests/test_docs_mirror.py と
// tests/test_horn_js_mirror.py が Node.js で照合する）。
//
// 値の表し方
// - 変数は Var、リテラルは Atom / Compare / Is / Not / Forall / Aggregate / Conj
// - 定数は JS の素の値（文字列・数値・真偽値）。Python の int と float の区別は持たない
//   （形式表記では整数値の float を int と同じに書くので、表記は一致する）
//
// ブラウザでも Node の vm でも動くように、モジュール構文は使わず globalThis.Horn に公開する。
(() => {
"use strict";

class HornError extends Error {
  constructor(message) {
    super(message);
    this.name = "HornError";
  }
}

// --- 項とリテラル ------------------------------------------------------------

class Var {
  constructor(name) { this.name = name; }
}

class Atom {
  constructor(predicate, args) { this.predicate = predicate; this.args = args; }
  get key() { return `${this.predicate}/${this.args.length}`; }
}

class Compare {
  constructor(op, left, right) { this.op = op; this.left = left; this.right = right; }
}

class Is {
  constructor(target, expr) { this.target = target; this.expr = expr; }
}

class Not {
  constructor(goal) { this.goal = goal; }
}

class Forall {
  constructor(condition, action) { this.condition = condition; this.action = action; }
}

class Aggregate {
  // kind: sum / count / max / min。count のとき template は null
  constructor(kind, template, goal, result) {
    this.kind = kind; this.template = template; this.goal = goal; this.result = result;
  }
}

class Conj {
  constructor(goals) { this.goals = goals; }
}

class BinOp {
  constructor(op, left, right) { this.op = op; this.left = left; this.right = right; }
}

class Func {
  constructor(name, args) { this.name = name; this.args = args; }
}

class Clause {
  // source: 原文の切り出し（頭部の先頭〜終端の '.' まで）
  constructor(head, body, source) { this.head = head; this.body = body; this.source = source; }
}

/** Atom を作る便利関数。 */
const atom = (predicate, ...args) => new Atom(predicate, args);

const COMPARE_OPS = [">=", ">", "=<", "<", "=", "\\="];
const ARITH_FUNCS = { max: 2, min: 2, ceiling: 1, floor: 1, round: 1, abs: 1 };
const AGGREGATE_KINDS = ["sum", "count", "max", "min"];
const isArithFunc = (name) => Object.prototype.hasOwnProperty.call(ARITH_FUNCS, name);

const NAME_SOURCE = "[a-z][A-Za-z0-9_]*(?::[A-Za-z0-9_][A-Za-z0-9_/\\-]*)?";
const NAME_FULL = new RegExp(`^(?:${NAME_SOURCE})$`);

// 改名した変数の接尾辞の区切り。変数名には現れない文字を使う
const RENAME_MARK = "#";
const ANONYMOUS_PREFIX = "_" + RENAME_MARK;

// Python の str.split() / 正規表現 \s が空白とみなす文字（JS の \s とは少し違う）
const PY_SPACE = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_SPACES = new RegExp(`[${PY_SPACE}]+`, "g");

// --- Python と表記を揃える小道具 ---------------------------------------------

const isNumber = (value) => typeof value === "number";
const isString = (value) => typeof value === "string";
const isBool = (value) => typeof value === "boolean";
const isConstant = (value) => isNumber(value) || isString(value) || isBool(value);

/** 整数値の数を Python の str(int(x)) と同じ桁で書く（1e21 以上も指数にしない）。 */
function integerText(value) {
  if (Math.abs(value) < 1e21) return String(value === 0 ? 0 : value);
  return BigInt(value).toString();
}

/** 整数値でない有限の数を Python の repr(float) と同じに書く。 */
function floatRepr(value) {
  if (Number.isNaN(value)) return "nan";
  if (!Number.isFinite(value)) return value > 0 ? "inf" : "-inf";
  const sign = value < 0 ? "-" : "";
  const [mantissa, exponentText] = Math.abs(value).toExponential().split("e");
  const digits = mantissa.replace(".", "");
  const exponent = Number(exponentText);
  if (exponent >= -4 && exponent < 16) {
    if (exponent >= 0) {
      const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, "0");
      const fraction = digits.slice(exponent + 1) || "0";
      return `${sign}${whole}.${fraction}`;
    }
    return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`;
  }
  const head = digits[0] + (digits.length > 1 ? "." + digits.slice(1) : "");
  const exponentSign = exponent < 0 ? "-" : "+";
  return `${sign}${head}e${exponentSign}${String(Math.abs(exponent)).padStart(2, "0")}`;
}

/** Python の str() と同じ表記（f 文字列に数値・真偽値を埋め込むとき用）。 */
function pyStr(value) {
  if (value === null || value === undefined) return "None";
  if (isBool(value)) return value ? "True" : "False";
  if (isNumber(value)) return Number.isInteger(value) ? integerText(value) : floatRepr(value);
  return String(value);
}

const NON_PRINTABLE = /^[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]$/u;

/** 1文字の Python repr（構文エラーの文言用）。 */
function reprChar(char) {
  if (char === "'") return `"'"`;
  if (char === "\\") return "'\\\\'";
  const escapes = { "\t": "\\t", "\n": "\\n", "\r": "\\r" };
  if (escapes[char]) return `'${escapes[char]}'`;
  if (char !== " " && NON_PRINTABLE.test(char)) {
    const code = char.codePointAt(0);
    const hex = code.toString(16);
    if (code < 0x100) return `'\\x${hex.padStart(2, "0")}'`;
    if (code < 0x10000) return `'\\u${hex.padStart(4, "0")}'`;
    return `'\\U${hex.padStart(8, "0")}'`;
  }
  return `'${char}'`;
}

/** Unicode の 10 進数字（Python の \d）の値。数字は 0〜9 の連続した 10 文字の並びで符号化されている。 */
function digitValue(char) {
  let code = char.codePointAt(0);
  if (code >= 0x30 && code <= 0x39) return code - 0x30;
  let offset = 0;
  while (/^\p{Nd}$/u.test(String.fromCodePoint(code - 1))) {
    code -= 1;
    offset += 1;
  }
  return offset % 10;
}

/** 数字の並び（Unicode の数字を含みうる）を Python の int()/float() と同じ値にする。 */
function numberValue(word) {
  const ascii = Array.from(word, (char) => (char === "." ? "." : String(digitValue(char)))).join("");
  return Number(ascii);
}

/** 文字列を Python と同じ（コードポイント順）に比べる。 */
function compareStrings(a, b) {
  const left = Array.from(a);
  const right = Array.from(b);
  const length = Math.min(left.length, right.length);
  for (let i = 0; i < length; i += 1) {
    const x = left[i].codePointAt(0);
    const y = right[i].codePointAt(0);
    if (x !== y) return x < y ? -1 : 1;
  }
  return left.length === right.length ? 0 : (left.length < right.length ? -1 : 1);
}

// --- 形式表記（Python 版と完全一致させる） -----------------------------------

function formatConstant(value) {
  if (isBool(value)) return value ? "true" : "false";
  if (isNumber(value)) {
    if (!Number.isFinite(value)) return floatRepr(value);
    if (Number.isInteger(value)) return integerText(value);
    // Math.round(x*1e6)/1e6 と同じ丸め（0.5 は正の方向へ）
    const rounded = Math.floor(value * 1e6 + 0.5) / 1e6;
    if (Number.isInteger(rounded)) return integerText(rounded);
    return floatRepr(rounded);
  }
  const text = pyStr(value);
  if (NAME_FULL.test(text)) return text;
  return "'" + text.replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
}

/** 変数をたどって束縛先へ置き換える（未束縛ならその変数）。 */
function walk(term, subst) {
  while (term instanceof Var && subst.has(term.name)) term = subst.get(term.name);
  return term;
}

const PRECEDENCE = { "+": 1, "-": 1, "*": 2, "/": 2 };

/** 二項演算の子。優先順位が親より低いか、右の子で同じ優先順位なら括弧で囲む（左結合）。 */
function formatOperand(child, parent, right, subst) {
  const text = formatExpr(child, subst);
  if (child instanceof BinOp) {
    const mine = PRECEDENCE[child.op];
    const theirs = PRECEDENCE[parent.op];
    if (mine < theirs || (right && mine === theirs)) return "(" + text + ")";
  }
  return text;
}

function formatExpr(expr, subst) {
  if (expr instanceof BinOp) {
    return formatOperand(expr.left, expr, false, subst) + " " + expr.op + " "
      + formatOperand(expr.right, expr, true, subst);
  }
  if (expr instanceof Func) {
    return expr.name + "(" + expr.args.map((a) => formatExpr(a, subst)).join(", ") + ")";
  }
  return formatTerm(expr, subst);
}

const EMPTY = new Map();

/** 項・式・リテラルを形式表記にする。subst（Map）があれば束縛を代入する。 */
function formatTerm(term, subst) {
  if (subst === undefined || subst === null) subst = EMPTY;
  if (term instanceof Var) {
    const value = walk(term, subst);
    if (value instanceof Var) return value.name.split(RENAME_MARK)[0];
    return formatConstant(value);
  }
  if (term instanceof Atom) {
    if (!term.args.length) return term.predicate;
    return term.predicate + "(" + term.args.map((a) => formatTerm(a, subst)).join(", ") + ")";
  }
  if (term instanceof Compare) {
    return formatExpr(term.left, subst) + " " + term.op + " " + formatExpr(term.right, subst);
  }
  if (term instanceof Is) {
    return formatTerm(term.target, subst) + " is " + formatExpr(term.expr, subst);
  }
  if (term instanceof BinOp || term instanceof Func) return formatExpr(term, subst);
  if (term instanceof Not) return "\\+ " + formatTerm(term.goal, subst);
  if (term instanceof Forall) {
    return "forall(" + formatTerm(term.condition, subst) + ", " + formatTerm(term.action, subst) + ")";
  }
  if (term instanceof Aggregate) {
    const spec = term.kind === "count" ? "count" : term.kind + "(" + formatExpr(term.template, subst) + ")";
    return "aggregate_all(" + spec + ", " + formatTerm(term.goal, subst) + ", "
      + formatTerm(term.result, subst) + ")";
  }
  if (term instanceof Conj) {
    return "(" + term.goals.map((g) => formatTerm(g, subst)).join(", ") + ")";
  }
  return formatConstant(term);
}

// --- 字句解析 ----------------------------------------------------------------

const TOKEN_SPECS = [
  ["number", /\p{Nd}+(?:\.\p{Nd}+)?/uy],
  ["var", /[A-Z_][A-Za-z0-9_]*/y],
  ["name", new RegExp(NAME_SOURCE, "y")],
  ["punct", /:-|\\\+|\\=|>=|=<|[><=(),.+\-*/]/y],
];

function token(kind, text, value, line, start, end) {
  return { kind, text, value, line, start, end };
}

function syntaxError(origin, line, message) {
  const prefix = origin ? `${origin} ` : "";
  return new HornError(`${prefix}${line}行目: 構文エラー: ${message}`);
}

function tokenize(text, origin) {
  const tokens = [];
  let pos = 0;
  let line = 1;
  while (pos < text.length) {
    const char = text[pos];
    if (char === "\n") {
      line += 1;
      pos += 1;
      continue;
    }
    if (char === " " || char === "\t" || char === "\r") {
      pos += 1;
      continue;
    }
    if (char === "%") {
      while (pos < text.length && text[pos] !== "\n") pos += 1;
      continue;
    }
    if (char === "'" || char === '"') {
      // 文字列。エスケープは \\ \' \" のみ
      const quote = char;
      const start = pos;
      pos += 1;
      const chars = [];
      for (;;) {
        if (pos >= text.length || text[pos] === "\n") {
          throw syntaxError(origin, line, "文字列が閉じていません");
        }
        const current = text[pos];
        if (current === "\\") {
          const following = pos + 1 < text.length ? String.fromCodePoint(text.codePointAt(pos + 1)) : "";
          if (!["\\", "'", '"'].includes(following)) {
            throw syntaxError(origin, line, `使えないエスケープです: \\${following}`);
          }
          chars.push(following);
          pos += 2;
          continue;
        }
        if (current === quote) {
          pos += 1;
          break;
        }
        chars.push(current);
        pos += 1;
      }
      tokens.push(token("string", text.slice(start, pos), chars.join(""), line, start, pos));
      continue;
    }
    let matched = false;
    for (const [kind, pattern] of TOKEN_SPECS) {
      pattern.lastIndex = pos;
      const match = pattern.exec(text);
      if (match) {
        const word = match[0];
        const value = kind === "number" ? numberValue(word) : word;
        tokens.push(token(kind, word, value, line, pos, pos + word.length));
        pos += word.length;
        matched = true;
        break;
      }
    }
    if (!matched) {
      throw syntaxError(origin, line, `解釈できない文字があります: ${reprChar(String.fromCodePoint(text.codePointAt(pos)))}`);
    }
  }
  tokens.push(token("end", "", null, line, text.length, text.length));
  return tokens;
}

// --- 構文解析 ----------------------------------------------------------------

class Parser {
  constructor(text, origin) {
    this.text = text;
    this.origin = origin;
    this.tokens = tokenize(text, origin);
    this.index = 0;
    this.anonymousCount = 0;
  }

  // 低レベル
  peek(offset = 0) {
    return this.tokens[Math.min(this.index + offset, this.tokens.length - 1)];
  }

  advance() {
    const current = this.tokens[this.index];
    if (current.kind !== "end") this.index += 1;
    return current;
  }

  at(text) {
    const current = this.peek();
    return current.kind === "punct" && current.text === text;
  }

  expect(text) {
    const current = this.peek();
    if (current.kind !== "punct" || current.text !== text) {
      throw this.error(`'${text}' が必要です（${this.describe(current)} があります）`);
    }
    return this.advance();
  }

  describe(current) {
    if (current.kind === "end") return "入力の終わり";
    return `'${current.text}'`;
  }

  error(message, at) {
    const current = at || this.peek();
    return syntaxError(this.origin, current.line, message);
  }

  // 節
  parseProgram() {
    const clauses = [];
    while (this.peek().kind !== "end") clauses.push(this.parseClause());
    return clauses;
  }

  parseClause() {
    const first = this.peek();
    if (first.kind !== "name") {
      throw this.error(`節の頭部は述語で始めます（${this.describe(first)} があります）`);
    }
    const head = this.parseAtom();
    let body = [];
    if (this.at(":-")) {
      this.advance();
      body = this.parseLiterals();
    }
    const last = this.expect(".");
    return new Clause(head, body, this.text.slice(first.start, last.end));
  }

  parseLiterals() {
    const literals = [this.parseLiteral()];
    while (this.at(",")) {
      this.advance();
      literals.push(this.parseLiteral());
    }
    return literals;
  }

  parseLiteral() {
    const current = this.peek();
    if (this.at("\\+")) {
      this.advance();
      return new Not(this.parseGoalArg());
    }
    if (current.kind === "name") {
      const following = this.peek(1);
      const opens = following.kind === "punct" && following.text === "(";
      if (opens && current.text === "not") {
        this.advance();
        this.expect("(");
        const goal = this.parseGoalArg();
        this.expect(")");
        return new Not(goal);
      }
      if (opens && current.text === "forall") {
        this.advance();
        this.expect("(");
        const condition = this.parseGoalArg();
        this.expect(",");
        const action = this.parseGoalArg();
        this.expect(")");
        return new Forall(condition, action);
      }
      if (opens && current.text === "aggregate_all") return this.parseAggregate();
      if (!isArithFunc(current.text)) {
        const result = this.parseAtom();
        const after = this.peek();
        if ((after.kind === "punct" && COMPARE_OPS.includes(after.text))
            || (after.kind === "name" && after.text === "is")) {
          throw this.error("アトムの後に比較演算子や is は書けません", after);
        }
        return result;
      }
    }
    const left = this.parseExpr();
    const operator = this.peek();
    if (operator.kind === "name" && operator.text === "is") {
      this.advance();
      if (!(left instanceof Var)) throw this.error("is の左辺は変数でなければなりません", operator);
      return new Is(left, this.parseExpr());
    }
    if (operator.kind === "punct" && COMPARE_OPS.includes(operator.text)) {
      this.advance();
      return new Compare(operator.text, left, this.parseExpr());
    }
    throw this.error(`比較演算子か is が必要です（${this.describe(operator)} があります）`);
  }

  /** 否定・forall・集約の中のゴール。アトムか括弧つき連言。 */
  parseGoalArg() {
    if (this.at("(")) {
      this.advance();
      const literals = this.parseLiterals();
      this.expect(")");
      if (literals.length === 1) return literals[0];
      return new Conj(literals);
    }
    return this.parseLiteral();
  }

  parseAggregate() {
    this.advance(); // aggregate_all
    this.expect("(");
    const spec = this.peek();
    if (spec.kind !== "name" || !AGGREGATE_KINDS.includes(spec.text)) {
      throw this.error("aggregate_all の第1引数は sum(T) / count / max(T) / min(T) です");
    }
    this.advance();
    let template = null;
    if (spec.text !== "count") {
      this.expect("(");
      template = this.parseExpr();
      this.expect(")");
    }
    this.expect(",");
    const goal = this.parseGoalArg();
    this.expect(",");
    const result = this.parseTerm();
    this.expect(")");
    return new Aggregate(spec.text, template, goal, result);
  }

  parseAtom() {
    const current = this.advance();
    if (current.kind !== "name") {
      throw this.error(`述語名が必要です（${this.describe(current)} があります）`, current);
    }
    const args = [];
    if (this.at("(")) {
      this.advance();
      args.push(this.parseTerm());
      while (this.at(",")) {
        this.advance();
        args.push(this.parseTerm());
      }
      this.expect(")");
    }
    return new Atom(current.text, args);
  }

  parseTerm() {
    const current = this.peek();
    if (current.kind === "var") {
      this.advance();
      if (current.text === "_") {
        // 無名変数は出現ごとに別物
        this.anonymousCount += 1;
        return new Var(`${ANONYMOUS_PREFIX}${this.anonymousCount}`);
      }
      return new Var(current.text);
    }
    if (current.kind === "number" || current.kind === "string" || current.kind === "name") {
      this.advance();
      if (current.kind === "name" && this.at("(")) {
        throw this.error(`引数に複合項は書けません: ${current.text}(...)`);
      }
      return current.value;
    }
    throw this.error(`項が必要です（${this.describe(current)} があります）`);
  }

  // 算術式
  parseExpr() {
    let left = this.parseProduct();
    while (this.at("+") || this.at("-")) {
      const op = this.advance().text;
      left = new BinOp(op, left, this.parseProduct());
    }
    return left;
  }

  parseProduct() {
    let left = this.parseFactor();
    while (this.at("*") || this.at("/")) {
      const op = this.advance().text;
      left = new BinOp(op, left, this.parseFactor());
    }
    return left;
  }

  parseFactor() {
    const current = this.peek();
    if (this.at("(")) {
      this.advance();
      const inner = this.parseExpr();
      this.expect(")");
      return inner;
    }
    if (current.kind === "name" && this.peek(1).kind === "punct" && this.peek(1).text === "(") {
      if (!isArithFunc(current.text)) throw this.error(`算術式に使えない関数です: ${current.text}`);
      this.advance();
      this.expect("(");
      const args = [this.parseExpr()];
      while (this.at(",")) {
        this.advance();
        args.push(this.parseExpr());
      }
      this.expect(")");
      if (args.length !== ARITH_FUNCS[current.text]) {
        throw this.error(`${current.text} の引数は ${ARITH_FUNCS[current.text]} 個です`, current);
      }
      return new Func(current.text, args);
    }
    return this.parseTerm();
  }
}

// --- 変数の収集と改名 --------------------------------------------------------

/** 項・リテラルに現れる変数名を出現順に集める（重複なし）。 */
function collectVars(term, out) {
  if (term instanceof Var) {
    if (!out.includes(term.name)) out.push(term.name);
  } else if (term instanceof Atom) {
    for (const arg of term.args) collectVars(arg, out);
  } else if (term instanceof Compare || term instanceof BinOp) {
    collectVars(term.left, out);
    collectVars(term.right, out);
  } else if (term instanceof Is) {
    collectVars(term.target, out);
    collectVars(term.expr, out);
  } else if (term instanceof Func) {
    for (const arg of term.args) collectVars(arg, out);
  } else if (term instanceof Not) {
    collectVars(term.goal, out);
  } else if (term instanceof Forall) {
    collectVars(term.condition, out);
    collectVars(term.action, out);
  } else if (term instanceof Aggregate) {
    if (term.template !== null) collectVars(term.template, out);
    collectVars(term.goal, out);
    collectVars(term.result, out);
  } else if (term instanceof Conj) {
    for (const goal of term.goals) collectVars(goal, out);
  }
}

/** 節を呼ぶたびに変数を新しい名前へ付け替える。 */
function rename(term, suffix) {
  if (term instanceof Var) return new Var(term.name + suffix);
  if (term instanceof Atom) return new Atom(term.predicate, term.args.map((a) => rename(a, suffix)));
  if (term instanceof Compare) return new Compare(term.op, rename(term.left, suffix), rename(term.right, suffix));
  if (term instanceof BinOp) return new BinOp(term.op, rename(term.left, suffix), rename(term.right, suffix));
  if (term instanceof Is) return new Is(rename(term.target, suffix), rename(term.expr, suffix));
  if (term instanceof Func) return new Func(term.name, term.args.map((a) => rename(a, suffix)));
  if (term instanceof Not) return new Not(rename(term.goal, suffix));
  if (term instanceof Forall) return new Forall(rename(term.condition, suffix), rename(term.action, suffix));
  if (term instanceof Aggregate) {
    const template = term.template === null ? null : rename(term.template, suffix);
    return new Aggregate(term.kind, template, rename(term.goal, suffix), rename(term.result, suffix));
  }
  if (term instanceof Conj) return new Conj(term.goals.map((g) => rename(g, suffix)));
  return term;
}

/** リテラル中のアトムを [アトム, 否定的か] で集める。 */
function atomsIn(term, negative, out) {
  if (term instanceof Atom) {
    out.push([term, negative]);
  } else if (term instanceof Not) {
    atomsIn(term.goal, true, out);
  } else if (term instanceof Forall) {
    atomsIn(term.condition, true, out);
    atomsIn(term.action, true, out);
  } else if (term instanceof Aggregate) {
    atomsIn(term.goal, true, out);
  } else if (term instanceof Conj) {
    for (const goal of term.goals) atomsIn(goal, negative, out);
  }
}

/**
 * 安全性検査で「頭部の変数を束縛できる」とみなす位置の変数を集める。
 * 本体のトップレベルの肯定アトム・is の左辺・集約の結果・= の両辺だけを数える。
 */
function bindingVars(literal, out) {
  if (literal instanceof Atom) {
    collectVars(literal, out);
  } else if (literal instanceof Is) {
    collectVars(literal.target, out);
  } else if (literal instanceof Compare) {
    if (literal.op === "=") {
      collectVars(literal.left, out);
      collectVars(literal.right, out);
    }
  } else if (literal instanceof Aggregate) {
    collectVars(literal.result, out);
  }
}

const oneLine = (source) => source.split(PY_SPACES).filter((part) => part !== "").join(" ");

// --- プログラム --------------------------------------------------------------

/** 節の集まり。述語キー（pred/arity）で索引する。 */
class Program {
  constructor(clauses = [], origins = {}) {
    this.clauses = [...clauses];
    // 述語キー → その述語を定義した origin（combine の重複検査に使う）
    this.origins = new Map(origins instanceof Map ? origins : Object.entries(origins));
    this.byKey = new Map();
    for (const clause of this.clauses) {
      if (!this.byKey.has(clause.head.key)) this.byKey.set(clause.head.key, []);
      this.byKey.get(clause.head.key).push(clause);
    }
  }

  static parse(text, origin = "") {
    const clauses = new Parser(text, origin).parseProgram();
    const origins = new Map(clauses.map((clause) => [clause.head.key, origin]));
    return new Program(clauses, origins);
  }

  static combine(programs) {
    const clauses = [];
    const origins = new Map();
    for (const program of programs) {
      for (const key of program.defined()) {
        const origin = program.origins.has(key) ? program.origins.get(key) : "";
        if (origins.has(key) && origins.get(key) !== origin) {
          throw new HornError(
            `述語 ${key} が ${origins.get(key)} と ${origin} の2箇所で定義されています`
            + "（1つの述語は1箇所で定義してください）");
        }
        origins.set(key, origin);
      }
      clauses.push(...program.clauses);
    }
    return new Program(clauses, origins);
  }

  defined() {
    return new Set(this.byKey.keys());
  }

  clausesFor(key) {
    return [...(this.byKey.get(key) || [])];
  }

  dependencies(key) {
    const result = [];
    for (const clause of this.byKey.get(key) || []) {
      const found = [];
      for (const literal of clause.body) atomsIn(literal, false, found);
      for (const [bodyAtom, negative] of found) {
        const dependency = bodyAtom.key;
        if (!result.some(([k, n]) => k === dependency && n === negative)) result.push([dependency, negative]);
      }
    }
    return result;
  }

  /** 依存グラフで start から target へ（肯定・否定を問わず）たどれるか。 */
  reaches(start, target) {
    const seen = [];
    const stack = [start];
    while (stack.length) {
      const key = stack.pop();
      if (key === target) return true;
      if (seen.includes(key)) continue;
      seen.push(key);
      for (const [dependency] of this.dependencies(key)) stack.push(dependency);
    }
    return false;
  }

  strata() {
    const keys = [...this.defined()].sort(compareStrings);
    // 否定的な辺が循環に含まれていないかを先に調べる
    for (const key of keys) {
      for (const [dependency, negative] of this.dependencies(key)) {
        if (negative && this.reaches(dependency, key)) {
          throw new HornError(
            `層化できません: ${key} が否定・forall・集約を通して ${dependency} に依存し、`
            + `${dependency} から ${key} へ戻る循環があります`);
        }
      }
    }
    // 層番号を反復で更新する（肯定の辺は同じ層以上、否定的な辺は1つ上の層）
    const levels = new Map(keys.map((key) => [key, 0]));
    let changed = true;
    while (changed) {
      changed = false;
      for (const key of keys) {
        for (const [dependency, negative] of this.dependencies(key)) {
          const below = levels.has(dependency) ? levels.get(dependency) : 0;
          const needed = negative ? below + 1 : below;
          if (levels.get(key) < needed) {
            levels.set(key, needed);
            changed = true;
          }
        }
      }
    }
    return Object.fromEntries(levels);
  }

  listing(goalKeys) {
    const sources = [];
    const visited = [];
    const visit = (key) => {
      if (visited.includes(key)) return;
      visited.push(key);
      for (const clause of this.byKey.get(key) || []) sources.push(clause.source);
      for (const [dependency] of this.dependencies(key)) visit(dependency);
    };
    for (const key of goalKeys) visit(key);
    return sources.join("\n");
  }

  check() {
    for (const clause of this.clauses) {
      const headVars = [];
      collectVars(clause.head, headVars);
      const bound = [];
      for (const literal of clause.body) bindingVars(literal, bound);
      const unsafe = headVars.filter((name) => !bound.includes(name)).map((name) => name.split(RENAME_MARK)[0]);
      if (unsafe.length) {
        throw new HornError(
          `安全でない節です（頭部の変数 ${unsafe.join(", ")} が本体で束縛されません）: `
          + oneLine(clause.source));
      }
    }
    this.strata();
  }
}

// --- 事実 --------------------------------------------------------------------

/** 事実の索引・重複判定に使うキー（true と 1、"2" と 2 を区別する）。 */
function indexKey(value) {
  if (isBool(value)) return "b:" + value;
  if (isNumber(value)) return "n:" + (value === 0 ? 0 : value);
  return "s:" + String(value);
}

/** 述語ごとの事実の集まり。同じ事実は1回だけ持つ（RDF のトリプルは集合）。 */
class FactBase {
  constructor(facts = []) {
    this.facts = new Map();
    this.byFirst = new Map();
    this.seen = new Set();
    for (const [predicate, args] of facts) this.add(predicate, args);
  }

  static fromJsonld(nodes) {
    const base = new FactBase();
    for (const node of nodes) {
      const subject = node["@id"];
      if (subject === undefined || subject === null) continue;
      let types = node["@type"] === undefined ? [] : node["@type"];
      if (!Array.isArray(types)) types = [types];
      for (const typeName of types) base.add("rdf:type", [subject, typeName]);
      for (const [key, raw] of Object.entries(node)) {
        if (key.startsWith("@")) continue;
        const values = Array.isArray(raw) ? raw : [raw];
        for (const value of values) {
          if (value !== null && typeof value === "object" && !Array.isArray(value)) {
            if ("@id" in value) base.add(key, [subject, value["@id"]]);
            else if ("@value" in value) base.add(key, [subject, value["@value"]]);
            // それ以外（入れ子の無名ノード等）は無視する
          } else if (isConstant(value)) {
            base.add(key, [subject, value]);
          }
        }
      }
    }
    return base;
  }

  add(predicate, args) {
    args = [...args];
    const key = `${predicate}/${args.length}`;
    const identity = JSON.stringify([key, ...args.map(indexKey)]);
    if (this.seen.has(identity)) return;
    this.seen.add(identity);
    if (!this.facts.has(key)) this.facts.set(key, []);
    this.facts.get(key).push(args);
    if (args.length) {
      if (!this.byFirst.has(key)) this.byFirst.set(key, new Map());
      const index = this.byFirst.get(key);
      const first = indexKey(args[0]);
      if (!index.has(first)) index.set(first, []);
      index.get(first).push(args);
    }
  }

  /** 述語の事実を返す。first を渡すと第1引数がそれに一致するものだけ。 */
  match(predicate, arity, first) {
    const key = `${predicate}/${arity}`;
    if (first === undefined || arity === 0) return [...(this.facts.get(key) || [])];
    const index = this.byFirst.get(key);
    return [...((index && index.get(indexKey(first))) || [])];
  }
}

// --- 証明木 ------------------------------------------------------------------

class ProofNode {
  // kind: rule / fact / builtin / not / forall / aggregate / conj
  constructor(goal, kind, proved, clause = null, detail = null, children = []) {
    this.goal = goal;
    this.kind = kind;
    this.proved = proved;
    this.clause = clause;
    this.detail = detail;
    this.children = children;
  }

  toDict() {
    return {
      goal: this.goal,
      kind: this.kind,
      proved: this.proved,
      clause: this.clause,
      detail: this.detail,
      children: this.children.map((child) => child.toDict()),
    };
  }
}

// --- 単一化と算術 ------------------------------------------------------------

function sameConstant(a, b) {
  if (isBool(a) || isBool(b)) return isBool(a) && isBool(b) && a === b;
  if (isNumber(a) && isNumber(b)) return a === b;
  if (isString(a) && isString(b)) return a === b;
  return false;
}

/** 単一化。成功すれば新しい束縛（元の Map は変えない）、失敗なら null。 */
function unify(a, b, subst) {
  a = walk(a, subst);
  b = walk(b, subst);
  if (a instanceof Var && b instanceof Var && a.name === b.name) return subst;
  if (a instanceof Var) {
    const extended = new Map(subst);
    extended.set(a.name, b);
    return extended;
  }
  if (b instanceof Var) {
    const extended = new Map(subst);
    extended.set(b.name, a);
    return extended;
  }
  return sameConstant(a, b) ? subst : null;
}

function unifyArgs(left, right, subst) {
  if (left.length !== right.length) return null;
  let current = subst;
  for (let i = 0; i < left.length; i += 1) {
    current = unify(left[i], right[i], current);
    if (current === null) return null;
  }
  return current;
}

function isGround(term, subst, allowAnonymous = false) {
  const names = [];
  collectVars(term, names);
  for (const name of names) {
    if (allowAnonymous && name.startsWith(ANONYMOUS_PREFIX)) continue; // 否定の内側の無名変数は許す
    if (walk(new Var(name), subst) instanceof Var) return false;
  }
  return true;
}

function number(value, context, subst) {
  if (!isNumber(value)) {
    throw new HornError(
      `数値でない値を算術に使っています（${formatConstant(value)}）: ${formatTerm(context, subst)}`);
  }
  return value;
}

/** 算術式を評価する。素の項は値をそのまま返す（文字列の比較に使う）。 */
function evaluate(expr, subst, context) {
  if (expr instanceof Var) {
    const value = walk(expr, subst);
    if (value instanceof Var) {
      throw new HornError(`式に未束縛の変数があります: ${formatTerm(context, subst)}`);
    }
    return value;
  }
  if (expr instanceof BinOp) {
    const left = number(evaluate(expr.left, subst, context), context, subst);
    const right = number(evaluate(expr.right, subst, context), context, subst);
    if (expr.op === "+") return left + right;
    if (expr.op === "-") return left - right;
    if (expr.op === "*") return left * right;
    if (right === 0) throw new HornError(`0 で割りました: ${formatTerm(context, subst)}`);
    return left / right;
  }
  if (expr instanceof Func) {
    const values = expr.args.map((a) => number(evaluate(a, subst, context), context, subst));
    if (expr.name === "max") return values[0] >= values[1] ? values[0] : values[1];
    if (expr.name === "min") return values[0] <= values[1] ? values[0] : values[1];
    if (expr.name === "ceiling") return Math.ceil(values[0]);
    if (expr.name === "floor") return Math.floor(values[0]);
    // Math.round と同じ（0.5 は正の方向へ）
    if (expr.name === "round") return Math.floor(values[0] + 0.5);
    return Math.abs(values[0]);
  }
  return expr;
}

// --- 評価器 ------------------------------------------------------------------

/** プログラムと事実の上で問い合わせを解く。 */
class Solver {
  constructor(program, facts, maxDepth = 64, maxSteps = 200000) {
    this.program = program;
    this.facts = facts;
    this.maxDepth = maxDepth;
    this.maxSteps = maxSteps;
    this.definedKeys = program.defined();
    this.steps = 0;
    this.fresh = 0;
  }

  // 公開 API
  *solve(goal) {
    this.start();
    const names = [];
    collectVars(goal, names);
    for (const [subst] of this.solveGoal(goal, new Map(), 0)) {
      // 変数名に __proto__ も書けるので、プロトタイプを持たない入れ物にする
      const answer = Object.create(null);
      for (const name of names) {
        if (name.startsWith(ANONYMOUS_PREFIX)) continue;
        const value = walk(new Var(name), subst);
        if (!(value instanceof Var)) answer[name] = value;
      }
      yield answer;
    }
  }

  holds(goal) {
    for (const _answer of this.solve(goal)) return true;
    return false;
  }

  value(goal, variable) {
    for (const answer of this.solve(goal)) {
      return Object.prototype.hasOwnProperty.call(answer, variable) ? answer[variable] : null;
    }
    return null;
  }

  explain(goal) {
    this.start();
    for (const [, node] of this.solveGoal(goal, new Map(), 0)) return node;
    return this.explainFailure(goal, new Map(), 0);
  }

  // 内部
  start() {
    this.steps = 0;
  }

  tick() {
    this.steps += 1;
    if (this.steps > this.maxSteps) throw new HornError(`手順数の上限（${this.maxSteps}）を超えました`);
  }

  /** ゴールの解を [束縛, 証明] の組で順に返す。 */
  *solveGoal(goal, subst, depth) {
    this.tick();
    if (goal instanceof Atom) {
      yield* this.solveAtom(goal, subst, depth);
    } else if (goal instanceof Conj) {
      for (const [result, children] of this.solveBody(goal.goals, 0, subst, depth)) {
        yield [result, new ProofNode(formatTerm(goal, result), "conj", true, null, null, children)];
      }
    } else if (goal instanceof Compare) {
      const result = this.compare(goal, subst);
      if (result !== null) yield [result, new ProofNode(formatTerm(goal, result), "builtin", true)];
    } else if (goal instanceof Is) {
      const value = evaluate(goal.expr, subst, goal);
      const result = unify(goal.target, value, subst);
      if (result !== null) yield [result, new ProofNode(formatTerm(goal, result), "builtin", true)];
    } else if (goal instanceof Not) {
      this.checkGround(goal, subst);
      for (const _found of this.solveGoal(goal.goal, subst, depth)) return;
      yield [subst, new ProofNode(formatTerm(goal, subst), "not", true)];
    } else if (goal instanceof Forall) {
      const children = this.forallChildren(goal, subst, depth);
      if (children !== null) {
        yield [subst, new ProofNode(formatTerm(goal, subst), "forall", true, null, null, children)];
      }
    } else if (goal instanceof Aggregate) {
      const [value, children] = this.aggregate(goal, subst, depth);
      if (value !== null) {
        const result = unify(goal.result, value, subst);
        if (result !== null) {
          yield [result, new ProofNode(
            formatTerm(goal, result), "aggregate", true, null, `${children.length}件`, children)];
        }
      }
    } else {
      throw new HornError(`ゴールとして評価できません: ${formatTerm(goal, subst)}`);
    }
  }

  *solveAtom(goal, subst, depth) {
    // 事実を先に、節を後に返す（同じ述語に両方あれば和）
    let first = goal.args.length ? walk(goal.args[0], subst) : undefined;
    if (first instanceof Var) first = undefined;
    for (const fact of this.facts.match(goal.predicate, goal.args.length, first)) {
      const result = unifyArgs(goal.args, fact, subst);
      if (result !== null) yield [result, new ProofNode(formatTerm(goal, result), "fact", true)];
    }
    if (!this.definedKeys.has(goal.key)) return;
    if (depth >= this.maxDepth) {
      throw new HornError(`深さの上限（${this.maxDepth}）を超えました: ${formatTerm(goal, subst)}`);
    }
    for (const clause of this.program.clausesFor(goal.key)) {
      const [head, body] = this.renamed(clause);
      const start = unifyArgs(goal.args, head.args, subst);
      if (start === null) continue;
      for (const [result, children] of this.solveBody(body, 0, start, depth + 1)) {
        yield [result, new ProofNode(
          formatTerm(goal, result), "rule", true, oneLine(clause.source), null, children)];
      }
    }
  }

  /** 本体のリテラルを左から順に解き、[束縛, 各リテラルの証明] を返す。 */
  *solveBody(body, index, subst, depth) {
    if (index === body.length) {
      yield [subst, []];
      return;
    }
    for (const [first, node] of this.solveGoal(body[index], subst, depth)) {
      for (const [result, rest] of this.solveBody(body, index + 1, first, depth)) {
        yield [result, [node, ...rest]];
      }
    }
  }

  renamed(clause) {
    this.fresh += 1;
    const suffix = `${RENAME_MARK}${this.fresh}`;
    return [rename(clause.head, suffix), clause.body.map((literal) => rename(literal, suffix))];
  }

  compare(goal, subst) {
    if (goal.op === "=") return unify(goal.left, goal.right, subst);
    if (goal.op === "\\=") return unify(goal.left, goal.right, subst) === null ? subst : null;
    if (!(isGround(goal.left, subst) && isGround(goal.right, subst))) {
      throw new HornError(`比較の両辺が束縛されていません: ${formatTerm(goal, subst)}`);
    }
    const left = evaluate(goal.left, subst, goal);
    const right = evaluate(goal.right, subst, goal);
    const bothNumbers = isNumber(left) && isNumber(right);
    const bothStrings = isString(left) && isString(right);
    if (!(bothNumbers || bothStrings)) {
      throw new HornError(`数値同士か文字列同士でなければ比較できません: ${formatTerm(goal, subst)}`);
    }
    // 文字列は Python と同じくコードポイント順で比べる
    const a = bothNumbers ? left : compareStrings(left, right);
    const b = bothNumbers ? right : 0;
    let ok;
    if (goal.op === ">=") ok = a >= b;
    else if (goal.op === ">") ok = a > b;
    else if (goal.op === "=<") ok = a <= b;
    else ok = a < b;
    return ok ? subst : null;
  }

  checkGround(goal, subst) {
    if (!isGround(goal.goal, subst, true)) {
      throw new HornError(`否定の対象に未束縛の変数があります: ${formatTerm(goal, subst)}`);
    }
  }

  /** C の各解について A の証明を集める。A に解の無い C の解があれば null。 */
  forallChildren(goal, subst, depth) {
    const children = [];
    for (const [conditionSubst] of this.solveGoal(goal.condition, subst, depth)) {
      let proof = null;
      for (const [, node] of this.solveGoal(goal.action, conditionSubst, depth)) {
        proof = node;
        break;
      }
      if (proof === null) return null;
      children.push(proof);
    }
    return children;
  }

  /** G の全解を集めて集約値を返す（重複も数える）。max/min が空なら値は null。 */
  aggregate(goal, subst, depth) {
    const children = [];
    const values = [];
    for (const [result, node] of this.solveGoal(goal.goal, subst, depth)) {
      children.push(node);
      if (goal.kind !== "count") {
        if (!isGround(goal.template, result)) {
          throw new HornError(`集約する値が束縛されていません: ${formatTerm(goal, result)}`);
        }
        values.push(number(evaluate(goal.template, result, goal), goal, result));
      }
    }
    if (goal.kind === "count") return [children.length, children];
    if (goal.kind === "sum") {
      let total = 0;
      for (const value of values) total += value;
      return [total, children];
    }
    if (!values.length) return [null, children];
    let best = values[0];
    for (const value of values.slice(1)) {
      if ((goal.kind === "max" && value > best) || (goal.kind === "min" && value < best)) best = value;
    }
    return [best, children];
  }

  // 失敗の説明
  firstProof(goal, subst, depth) {
    for (const found of this.solveGoal(goal, subst, depth)) return found;
    return null;
  }

  /** 解をもつ最長の接頭辞 body[:i] を探し、[i, 接頭辞の証明, その束縛] を返す。 */
  longestPrefix(body, subst, depth) {
    for (let length = body.length - 1; length > 0; length -= 1) {
      for (const [result, children] of this.solveBody(body.slice(0, length), 0, subst, depth)) {
        return [length, children, result];
      }
    }
    return [0, [], subst];
  }

  /** 解の無いゴールについて、どこで行き詰まったかを木にする。 */
  explainFailure(goal, subst, depth) {
    const text = formatTerm(goal, subst);
    if (goal instanceof Atom) {
      if (!this.definedKeys.has(goal.key)) return new ProofNode(text, "fact", false, null, "事実がない");
      let best = null;
      for (const clause of this.program.clausesFor(goal.key)) {
        const [head, body] = this.renamed(clause);
        const start = unifyArgs(goal.args, head.args, subst);
        if (start === null) continue;
        const [length, children, reached] = this.longestPrefix(body, start, depth + 1);
        if (best === null || length > best[0]) best = [length, children, reached, body, clause];
      }
      if (best === null) return new ProofNode(text, "rule", false, null, "該当する節がない");
      const [length, children, reached, body, clause] = best;
      const all = [...children, this.explainFailure(body[length], reached, depth + 1)];
      return new ProofNode(text, "rule", false, oneLine(clause.source), null, all);
    }
    if (goal instanceof Conj) {
      const [length, children, reached] = this.longestPrefix(goal.goals, subst, depth);
      const all = [...children, this.explainFailure(goal.goals[length], reached, depth)];
      return new ProofNode(text, "conj", false, null, null, all);
    }
    if (goal instanceof Compare || goal instanceof Is) return new ProofNode(text, "builtin", false);
    if (goal instanceof Not) {
      this.checkGround(goal, subst);
      const found = this.firstProof(goal.goal, subst, depth);
      return new ProofNode(text, "not", false, null, null, found === null ? [] : [found[1]]);
    }
    if (goal instanceof Forall) {
      const children = [];
      for (const [conditionSubst] of this.solveGoal(goal.condition, subst, depth)) {
        const found = this.firstProof(goal.action, conditionSubst, depth);
        children.push(found === null ? this.explainFailure(goal.action, conditionSubst, depth) : found[1]);
      }
      return new ProofNode(text, "forall", false, null, null, children);
    }
    if (goal instanceof Aggregate) {
      const [, children] = this.aggregate(goal, subst, depth);
      return new ProofNode(text, "aggregate", false, null, `${children.length}件`, children);
    }
    throw new HornError(`ゴールとして評価できません: ${text}`);
  }
}

/** 形式表記の文字列をゴール（アトムか括弧つき連言など）へ読む。 */
const parseGoal = (text) => new Parser(text, "").parseGoalArg();

// --- 読み下し（src/horn_reading.py） -----------------------------------------

const COMPARE_WORDS = {
  ">=": "以上",
  ">": "より大きい",
  "=<": "以下",
  "<": "未満",
  "=": "と等しい",
  "\\=": "と等しくない",
};

const AGGREGATE_WORDS = { sum: "合計", max: "最大値", min: "最小値" };
const PLACEHOLDER = /\{(\p{Nd}+)\}/gu;

function fill(template, names) {
  return template.replace(PLACEHOLDER, (_whole, digits) => {
    // Python の names[int(n) - 1]（0 なら末尾、範囲外はエラー）
    let index = numberValue(digits) - 1;
    if (index < 0) index += names.length;
    if (index < 0 || index >= names.length) throw new RangeError("list index out of range");
    return names[index];
  });
}

/** 「{text}、である{tail}」。text が「である」で終わるときは「、である」を省く。 */
function connect(text, tail) {
  if (text.endsWith("である")) return text + tail;
  return text + "、である" + tail;
}

/**
 * 読み下し器を作る。horn_reading.py のモジュール変数（GRAPH・HORN_PROGRAM）に当たるものを渡す。
 *   program   … 実行される Program（HORN_PROGRAM）
 *   ruleTypes … rule_type → {readings, variables}（グラフ文書の並び順）
 *   terms     … CURIE → {id, label, reading, variables}（variables は定義節を持つ用語だけ。他は null）
 *   nameOf    … IRI → ナレッジグラフ上の schema:name（無ければ null）。省略可
 */
function createReader({ program, ruleTypes, terms, nameOf }) {
  const termOf = (curie) => (Object.prototype.hasOwnProperty.call(terms, curie) ? terms[curie] : null);

  // 述語キー → その述語を定義したノード（rule-type / term）の変数の呼び名
  const byNode = new Map();
  for (const [ruleType, meta] of Object.entries(ruleTypes)) {
    byNode.set(`rule-type/${ruleType}`, { ...(meta.variables || {}) });
  }
  for (const term of Object.values(terms)) {
    if (term.variables !== null && term.variables !== undefined) byNode.set(term.id, { ...term.variables });
  }
  const VARIABLES = new Map();
  for (const [key, origin] of program.origins) {
    if (byNode.has(origin)) VARIABLES.set(key, byNode.get(origin));
  }

  // 述語キー → 読み下しの型（全 rule-type の readings を合わせたもの）
  const READINGS = new Map();
  for (const meta of Object.values(ruleTypes)) {
    for (const [key, template] of Object.entries(meta.readings || {})) READINGS.set(key, template);
  }

  const variablesFor = (key) => ({ ...(VARIABLES.get(key) || {}) });

  class Namer {
    constructor(variables, graph) {
      this.variables = variables;
      this.graph = graph;
    }

    name(term) {
      if (term instanceof Var) {
        const base = formatTerm(term);
        const known = Object.prototype.hasOwnProperty.call(this.variables, base);
        return "〈" + (known ? this.variables[base] : base) + "〉";
      }
      if (isString(term)) {
        if (this.graph) {
          const name = this.graph(term);
          if (name !== null && name !== undefined) return String(name);
        }
        const definition = termOf(term);
        if (definition) return definition.label;
      }
      return formatConstant(term);
    }

    /** 式の形式表記（変数・定数だけ呼び名に置き換える）。 */
    expr(expr) {
      if (expr instanceof BinOp) {
        return this.operand(expr.left, expr, false) + " " + expr.op + " " + this.operand(expr.right, expr, true);
      }
      if (expr instanceof Func) return expr.name + "(" + expr.args.map((a) => this.expr(a)).join(", ") + ")";
      return this.name(expr);
    }

    operand(child, parent, right) {
      const text = this.expr(child);
      if (child instanceof BinOp) {
        const mine = PRECEDENCE[child.op];
        const theirs = PRECEDENCE[parent.op];
        if (mine < theirs || (right && mine === theirs)) return "(" + text + ")";
      }
      return text;
    }
  }

  function readAtom(goal, namer) {
    const names = goal.args.map((arg) => namer.name(arg));
    if (READINGS.has(goal.key)) return fill(READINGS.get(goal.key), names);
    if (goal.predicate === "rdf:type" && names.length === 2) return `${names[0]}は${names[1]}である`;
    const definition = termOf(goal.predicate);
    if (names.length === 2 && definition) {
      if (definition.reading !== null && definition.reading !== undefined) return fill(definition.reading, names);
      return `${names[0]}の${definition.label}は${names[1]}`;
    }
    if (!names.length) return goal.predicate;
    return goal.predicate + "(" + names.join(", ") + ")";
  }

  function readLiteral(literal, namer) {
    if (literal instanceof Atom) return readAtom(literal, namer);
    if (literal instanceof Compare) {
      return `${namer.expr(literal.left)}が${namer.expr(literal.right)}${COMPARE_WORDS[literal.op]}`;
    }
    if (literal instanceof Is) return `${namer.expr(literal.target)}は ${namer.expr(literal.expr)} で求める`;
    if (literal instanceof Aggregate) {
      const goal = readLiteral(literal.goal, namer);
      const result = namer.expr(literal.result);
      if (literal.kind === "count") return connect(goal, `ようなものの件数は${result}`);
      const word = AGGREGATE_WORDS[literal.kind];
      return connect(goal, `ような${namer.expr(literal.template)}の${word}は${result}`);
    }
    if (literal instanceof Forall) {
      return connect(readLiteral(literal.condition, namer),
        `ものすべてについて、${readLiteral(literal.action, namer)}`);
    }
    if (literal instanceof Not) return `${readLiteral(literal.goal, namer)}、ということはない`;
    if (literal instanceof Conj) return literal.goals.map((goal) => readLiteral(goal, namer)).join("、かつ");
    return namer.name(literal);
  }

  /** 節を {head: 頭部の読み, conditions: [本体各リテラルの読み]} にする。 */
  function readClause(clause, graph = nameOf) {
    const namer = new Namer(variablesFor(clause.head.key), graph);
    return {
      head: readLiteral(clause.head, namer),
      conditions: clause.body.map((literal) => readLiteral(literal, namer)),
    };
  }

  /** rule-type が定義する節すべての読み下し（節の並びはグラフ文書のとおり）。 */
  function readRuleType(ruleType, graph = nameOf) {
    const origin = `rule-type/${ruleType}`;
    const readings = [];
    for (const clause of program.clauses) {
      if (program.origins.get(clause.head.key) === origin) {
        readings.push({ source: clause.source, ...readClause(clause, graph) });
      }
    }
    return readings;
  }

  /** 証明木の goal（形式表記の文字列）をリテラルへ戻す。 */
  function parseProofGoal(text, kind) {
    const parser = new Parser(text, "");
    if (kind === "builtin") {
      // 代入後は is の左辺も定数になるので、式・演算子・式として読む
      const left = parser.parseExpr();
      const operator = parser.advance();
      const right = parser.parseExpr();
      if (operator.text === "is") return new Is(left, right);
      return new Compare(operator.text, left, right);
    }
    return parser.parseGoalArg();
  }

  /** 証明木（ProofNode.toDict()）の各ノードに reading を付けた写しを返す。 */
  function annotate(proof, graph = nameOf, variables = null) {
    let literal;
    try {
      literal = parseProofGoal(String(proof.goal), String(proof.kind));
    } catch (error) {
      if (!(error instanceof HornError)) throw error;
      literal = null;
    }
    if (variables === null) variables = literal instanceof Atom ? variablesFor(literal.key) : {};
    const namer = new Namer(variables, graph);
    const reading = literal !== null ? readLiteral(literal, namer) : String(proof.goal);
    let inner = variables;
    if (proof.kind === "rule" && literal instanceof Atom && VARIABLES.has(literal.key)) {
      inner = variablesFor(literal.key);
    }
    return {
      ...proof,
      reading,
      children: proof.children.map((child) => annotate(child, graph, inner)),
    };
  }

  /** 証明木の根の述語キー（例 "eligible_to_register/2"）。 */
  function proofGoalKey(proof) {
    let literal;
    try {
      literal = parseProofGoal(String(proof.goal), String(proof.kind));
    } catch (error) {
      if (!(error instanceof HornError)) throw error;
      return null;
    }
    return literal instanceof Atom ? literal.key : null;
  }

  /** 述語キーを定義する節すべての読み下し。 */
  function readPredicate(key, graph = nameOf) {
    return program.clausesFor(key).map((clause) => ({ source: clause.source, ...readClause(clause, graph) }));
  }

  return {
    variablesFor, readLiteral, readClause, readRuleType, annotate, proofGoalKey, readPredicate,
    namer: (variables, graph = nameOf) => new Namer(variables, graph),
  };
}

// --- 証明木の HTML -----------------------------------------------------------

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function proofLine(node) {
  const mark = node.proved ? "✅" : "❌";
  const detail = node.detail ? `（${escapeHtml(node.detail)}）` : "";
  const text = node.reading || node.goal;
  return `${mark} ${escapeHtml(text)}${detail} <small><code>${escapeHtml(node.goal)}</code></small>`;
}

function proofNodeHtml(node, depth) {
  const indent = `margin-left: ${depth === 0 ? 0 : 1.4}em`;
  if (!node.children || !node.children.length) {
    return `<div class="proof-node" style="${indent}">${proofLine(node)}</div>`;
  }
  // 失敗した枝は開いておき、成功した枝は畳む（どこで行き詰まったかを先に見せる）
  const open = node.proved && depth > 0 ? "" : " open";
  return `<details class="proof-node" style="${indent}"${open}><summary>${proofLine(node)}</summary>`
    + node.children.map((child) => proofNodeHtml(child, depth + 1)).join("")
    + "</details>";
}

/**
 * 証明木（読み下し reading つき）を HTML 文字列にする。DOM には触れない。
 * 各行は読み下しを主に、形式表記を小さく併記し、子は <details> で畳む（app.py の証明木と同じ方針）。
 */
function renderProofHtml(proof) {
  if (!proof) return "";
  return '<details class="proof"><summary><strong>証明木</strong>（処理系が実際にたどった導出）</summary>'
    + proofNodeHtml(proof, 0) + "</details>";
}

globalThis.Horn = {
  HornError,
  Var, Atom, Compare, Is, Not, Forall, Aggregate, Conj, BinOp, Func, Clause,
  atom,
  formatConstant, formatTerm, pyStr, compareStrings,
  Program, FactBase, Solver, ProofNode,
  parseGoal,
  createReader,
  renderProofHtml,
  escapeHtml,
};
})();
