// docs/assets/horn.js（Horn 節の処理系の JS 版）を Node.js で動かし、ケースごとの結果を
// JSON で標準出力へ書き出す。tests/test_horn_js_mirror.py が、同じケースを src/horn.py で
// 評価した結果と突き合わせる。
//
//   node tests/js/check_horn.mjs <docs/assets/horn.js> <cases.json>

import { readFileSync } from "node:fs";
import vm from "node:vm";

const [hornPath, casesPath] = process.argv.slice(2);
vm.runInThisContext(readFileSync(hornPath, "utf8"), { filename: "docs/assets/horn.js" });
const cases = JSON.parse(readFileSync(casesPath, "utf8"));
const { Program, FactBase, Solver, HornError, formatConstant, formatTerm, parseGoal } = globalThis.Horn;

// HornError の文言を返す。それ以外の例外はそのまま落とす（処理系の不具合なので）
function attempt(action) {
  try {
    return { ok: action() };
  } catch (error) {
    if (error instanceof HornError) return { error: error.message };
    throw error;
  }
}

const programOf = (sources) => Program.combine(sources.map(([origin, text]) => Program.parse(text, origin)));
const plain = (answer) => Object.fromEntries(Object.entries(answer));

function runQuery(query) {
  return attempt(() => {
    const program = programOf(query.program);
    program.check();
    const solver = new Solver(program, new FactBase(query.facts), query.max_depth, query.max_steps);
    const goal = parseGoal(query.goal);
    if (query.mode === "explain") return solver.explain(goal).toDict();
    if (query.mode === "holds") return solver.holds(goal);
    return [...solver.solve(goal)].map(plain);
  });
}

const output = {
  syntax: cases.syntax.map(([origin, text]) => attempt(() => {
    const program = Program.parse(text, origin);
    return program.clauses.map((clause) => [
      formatTerm(clause.head), clause.body.map((literal) => formatTerm(literal)), clause.source,
    ]);
  })),
  constants: cases.constants.map((value) => formatConstant(value)),
  checks: cases.checks.map((sources) => attempt(() => {
    const program = programOf(sources);
    program.check();
    return { defined: [...program.defined()].sort(), strata: program.strata() };
  })),
  listings: cases.listings.map(([sources, keys]) => attempt(() => programOf(sources).listing(keys))),
  queries: cases.queries.map(runQuery),
};
process.stdout.write(JSON.stringify(output, null, 1));
