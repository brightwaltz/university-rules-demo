// docs/examples.html の描画スクリプトを、最小限のDOMスタブの上で実行するスモークテスト。
// ブラウザを起動せずに「読み込んで描ける」「絞り込み・判断の入力・書き出しで落ちない」ことを確かめる。
//
//   node tests/js/check_examples_render.mjs <docs/examples.html> [docs/assets/horn.js]
//
// horn.js を渡すと、証明木を Horn.renderProofHtml で描く。渡さなければページ内の簡易版を使う。

import { readFileSync } from "node:fs";
import vm from "node:vm";

const [htmlPath, hornPath] = process.argv.slice(2);
const html = readFileSync(htmlPath, "utf8");

const blocks = new Map();
for (const match of html.matchAll(/<script[^>]*\bid="([a-z-]+)"[^>]*>([\s\S]*?)<\/script>/g)) {
  blocks.set(match[1], match[2]);
}

// --- 最小DOMスタブ ---------------------------------------------------------

const byId = new Map();

class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.dataset = {};
    this.listeners = {};
    this._text = "";
    this._html = "";
    this.value = "";
    this.hidden = true;
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name === "id") byId.set(value, this);
  }
  getAttribute(name) { return this.attributes[name]; }
  appendChild(child) { this.children.push(child); return child; }
  remove() {}
  click() {}
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  dispatch(type, event) { for (const handler of this.listeners[type] || []) handler(event); }
  scrollIntoView() {}
  select() {}
  get textContent() { return this._text; }
  set textContent(value) { this._text = value; }
  get innerHTML() { return this._html; }
  set innerHTML(value) {
    this._html = value;
    // 表の行（<tr id="row-…">）は、描き直しを確かめられるよう要素として登録する
    for (const match of String(value).matchAll(/<tr id="(row-[^"]+)">/g)) {
      const row = byId.get(match[1]) || new El("tr");
      row.setAttribute("id", match[1]);
    }
  }
}

for (const id of [
  "counts", "filter-rule", "filter-origin", "filter-state", "reviewer", "export-reviews", "reset",
  "global-status", "export-panel", "export-caption", "export-copy", "export-download", "export-close",
  "export-note", "export-text", "shown-count", "examples-body", "example-detail",
]) new El("div").setAttribute("id", id);

globalThis.document = {
  getElementById: (id) => {
    if (blocks.has(id)) return { textContent: blocks.get(id) };
    return byId.get(id) || null;
  },
  querySelector: (selector) => {
    if (selector.startsWith("#")) return byId.get(selector.slice(1)) || new El("stub");
    return new El("stub");
  },
  createElement: (tag) => new El(tag),
  body: new El("body"),
};
globalThis.window = globalThis;
globalThis.localStorage = {
  store: new Map(),
  getItem(key) { return this.store.get(key) ?? null; },
  setItem(key, value) { this.store.set(key, value); },
  removeItem(key) { this.store.delete(key); },
};
globalThis.confirm = () => true;
globalThis.alert = () => {};
globalThis.Blob = class { constructor(parts) { this.parts = parts; } };
globalThis.URL = { createObjectURL: () => "blob:stub", revokeObjectURL() {} };

// --- 実行 -------------------------------------------------------------------

const view = blocks.get("examples-view");
if (!view || !blocks.has("examples")) {
  console.error("examples / examples-view が見つかりません。");
  process.exit(1);
}

const results = [];
const step = (name, fn) => {
  try {
    const value = fn();
    results.push({ step: name, ok: true, detail: value ?? null });
  } catch (error) {
    results.push({ step: name, ok: false, detail: `${error.name}: ${error.message}` });
  }
};
const probe = (expression) => vm.runInThisContext(expression, { filename: "probe" });
const expect = (condition, message) => { if (!condition) throw new Error(message); };
const body = () => byId.get("examples-body").innerHTML;

if (hornPath) {
  step("horn.js 読み込み", () => {
    vm.runInThisContext(readFileSync(hornPath, "utf8"), { filename: "docs/assets/horn.js" });
    expect(globalThis.Horn && typeof globalThis.Horn.renderProofHtml === "function",
      "Horn.renderProofHtml がありません");
    return "Horn.renderProofHtml";
  });
}

step("初期描画（スクリプト読み込み）", () => {
  vm.runInThisContext(view, { filename: "docs/examples.html#examples-view" });
  return probe("shownIds.length");
});
step("件数の表示", () => {
  const counts = byId.get("counts").innerHTML;
  expect(counts.includes("全件") && counts.includes("未確認"), "件数が描かれていません");
  return counts.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
});
step("全件が表に出ている", () => {
  const rows = (body().match(/<tr id=/g) || []).length;
  expect(rows === probe("EMBEDDED.length"), `${rows} 行しかありません`);
  return rows;
});
step("並び順（未確認かつ不一致 → 未確認 → 確認済み）", () => {
  const states = probe("shownIds.map((id) => stateOf(byId.get(id)))");
  for (let i = 1; i < states.length; i += 1) expect(states[i - 1] <= states[i], "並び順が崩れています");
  return probe("shownIds.slice(0, 3).join(', ')");
});
step("絞り込み（ルール）", () => {
  byId.get("filter-rule").value = "thesis_eligibility";
  const count = probe("applyFilters()");
  expect(count > 0 && probe("shownIds.every((id) => byId.get(id).rule_type === 'thesis_eligibility')"),
    "ルールで絞り込めていません");
  byId.get("filter-rule").value = "all";
  return count;
});
step("絞り込み（出どころ）", () => {
  byId.get("filter-origin").value = "ai";
  const count = probe("applyFilters()");
  expect(count > 0 && probe("shownIds.every((id) => byId.get(id).origin === 'ai')"), "出どころで絞り込めていません");
  byId.get("filter-origin").value = "all";
  return count;
});
step("絞り込み（状態：システムと不一致）", () => {
  byId.get("filter-state").value = "mismatch";
  const count = probe("applyFilters()");
  byId.get("filter-state").value = "all";
  probe("applyFilters()");
  return count;
});
step("詳細表示（証明木つき）", () => {
  const id = probe("EMBEDDED[0].id");
  probe(`selectExample(${JSON.stringify(id)})`);
  const detail = byId.get("example-detail").innerHTML;
  expect(detail.includes("証明木"), "証明木がありません");
  expect(detail.includes("schema:text"), "条文の本文がありません");
  return detail.length;
});
step("ai_note の表示", () => {
  probe("selectExample(EMBEDDED.find((item) => item.ai_note).id)");
  expect(byId.get("example-detail").innerHTML.includes("AI が気づいた解釈上の疑問"), "ai_note が出ていません");
  return probe("selectedId");
});
step("判断の入力（イベント経由）", () => {
  const id = probe("EMBEDDED[0].id");
  byId.get("examples-body").dispatch("change", { target: { dataset: { id, field: "verdict" }, value: "agree" } });
  expect(probe(`reviews.get(${JSON.stringify(id)}).verdict`) === "agree", "判断が反映されていません");
  expect(byId.get(`row-${id}`).innerHTML.includes('value="agree" selected'), "行が描き直されていません");
  return probe("changedIds().join(', ')");
});
step("「正しくない」で正しい結論が未選択なら書き出さない", () => {
  probe('setReview(EMBEDDED[1].id, { verdict: "disagree", correct_status: null })');
  const result = probe("exportReviews()");
  expect(result === null, "書き出されてしまいました");
  expect(byId.get("global-status").innerHTML.includes(probe("EMBEDDED[1].id")), "未選択の例題が示されていません");
  return byId.get("global-status").innerHTML.replace(/<[^>]+>/g, "");
});
step("書き出し", () => {
  probe('setReview(EMBEDDED[1].id, { correct_status: "unknown", note: "  確認した  " })');
  byId.get("reviewer").value = "担当者";
  const payload = probe("exportReviews()");
  expect(payload && payload.examples.length === 2, "変更した2件が書き出されていません");
  const text = byId.get("export-text").value;
  expect(byId.get("export-panel").hidden === false, "書き出しパネルが開いていません");
  expect(JSON.parse(text).examples.length === 2, "本文が JSON ではありません");
  return text;
});
step("「正しくない」以外にすると正しい結論を消す", () => {
  probe('setReview(EMBEDDED[1].id, { verdict: "unsure" })');
  expect(probe("reviews.get(EMBEDDED[1].id).correct_status") === null, "correct_status が残っています");
  return probe("JSON.stringify(reviews.get(EMBEDDED[1].id))");
});
step("localStorage への保存と読み直し", () => {
  const stored = JSON.parse(localStorage.getItem("university_example_reviews"));
  expect(Object.keys(stored.reviews).length === 2, "変更した例題が保存されていません");
  expect(probe("loadReviews().get(EMBEDDED[1].id).verdict") === "unsure", "読み直せません");
  return Object.keys(stored.reviews).join(", ");
});
step("ダウンロード", () => {
  byId.get("export-download").onclick();
  return byId.get("export-note").textContent;
});
step("入力を破棄", () => {
  byId.get("reset").dispatch("click", {});
  expect(probe("changedIds().length") === 0, "破棄されていません");
  return probe("JSON.stringify(exportReviews())");
});

process.stdout.write(JSON.stringify(results, null, 1));
