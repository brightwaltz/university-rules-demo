// docs/graph.html の描画スクリプトを、最小限のDOMスタブの上で実行するスモークテスト。
// ブラウザを起動せずに「読み込んで描ける」「操作しても落ちない」ことを確かめる。
// 見た目までは検証できないので、例外が出ないことと、SVGに要素が積まれることを見る。
//
//   node tests/js/check_graph_render.mjs <docs/graph.html>

import { readFileSync } from "node:fs";
import vm from "node:vm";

const [htmlPath] = process.argv.slice(2);
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
    this.classList = { add() {}, remove() {}, contains: () => false };
    this._text = "";
    this._html = "";
    this.value = "";
    this.checked = true;
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name === "id") byId.set(value, this);
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-./g, (m) => m[1].toUpperCase())] = value;
  }
  getAttribute(name) { return this.attributes[name]; }
  hasAttribute(name) { return name in this.attributes; }
  appendChild(child) { this.children.push(child); return child; }
  remove() {}
  addEventListener() {}
  removeEventListener() {}
  setPointerCapture() {}
  scrollIntoView() {}
  querySelector() { return new El("stub"); }
  querySelectorAll() { return []; }
  select() {}
  focus() {}
  get selectedOptions() { return []; }
  getBoundingClientRect() { return { width: 900, height: 620, left: 0, top: 0 }; }
  get textContent() { return this._text; }
  set textContent(value) { this._text = value; this.children = []; }
  get innerHTML() { return this._html; }
  set innerHTML(value) { this._html = value; }
  // 描画された要素数を数えるための補助
  countDeep() {
    return this.children.reduce((total, child) => total + 1 + child.countDeep(), 0);
  }
}

function makeElement(id) {
  const element = new El("div");
  element.setAttribute("id", id);
  return element;
}

for (const id of [
  "search", "grouping", "show-derived", "show-rationale", "relayout", "fit",
  "add-node", "add-edge", "add-hyper", "export-yaml", "export-json", "reset",
  "tree", "legend", "doc-meta", "graph-canvas", "graph-stats", "inspector", "global-status",
  "export-panel", "export-text", "export-caption", "export-note",
  "export-copy", "export-download", "export-close",
  "layout", "focus-mode",
]) makeElement(id);

byId.get("grouping").value = "layer";
byId.get("layout").value = "grid";

globalThis.document = {
  getElementById: (id) => {
    if (blocks.has(id)) return { textContent: blocks.get(id) };
    return byId.get(id) || null;
  },
  querySelector: (selector) => {
    if (selector.startsWith("#")) return byId.get(selector.slice(1)) || new El("stub");
    return new El("stub");
  },
  createElementNS: (_ns, tag) => new El(tag),
  createElement: (tag) => new El(tag),
  body: new El("body"),
};
globalThis.window = { addEventListener() {}, removeEventListener() {} };
globalThis.localStorage = {
  store: new Map(),
  getItem(key) { return this.store.get(key) ?? null; },
  setItem(key, value) { this.store.set(key, value); },
  removeItem(key) { this.store.delete(key); },
};
globalThis.confirm = () => true;
globalThis.alert = () => {};
globalThis.prompt = (_message, value) => value ?? "";
globalThis.Blob = class { constructor(parts) { this.parts = parts; } };
globalThis.URL = { createObjectURL: () => "blob:stub", revokeObjectURL() {} };

// --- 実行 -------------------------------------------------------------------

const engine = blocks.get("graph-engine");
const view = blocks.get("graph-view");
if (!engine || !view) {
  console.error("graph-engine / graph-view が見つかりません。");
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

vm.runInThisContext(engine, { filename: "docs/graph.html#graph-engine" });

step("初期描画（スクリプト読み込み）", () => {
  vm.runInThisContext(view, { filename: "docs/graph.html#graph-view" });
  return byId.get("graph-canvas").countDeep();
});

const probe = (expression) => vm.runInThisContext(expression, { filename: "probe" });

step("ノード・エッジが描かれている", () => byId.get("graph-canvas").countDeep());
step("統計表示", () => byId.get("graph-stats").textContent);
step("階層ツリー生成", () => byId.get("tree").innerHTML.length);
step("凡例生成", () => byId.get("legend").innerHTML.length);
step("ノード選択", () => { probe('select("node", "concept/course")'); return byId.get("inspector").innerHTML.length; });
step("ハイパーノード選択", () => { probe('select("hypernode", "rationale/why-ccso")'); return byId.get("inspector").innerHTML.length; });
step("エッジ選択", () => {
  probe('select("edge", doc.allEdges()[0].id)');
  return byId.get("inspector").innerHTML.length;
});
step("折りたたみ", () => { probe('toggleCollapse("layer/ccso")'); return byId.get("graph-stats").textContent; });
step("展開", () => { probe('toggleCollapse("layer/ccso")'); return byId.get("graph-stats").textContent; });
step("グルーピング切替（業務領域）", () => {
  byId.get("grouping").value = "domain";
  probe('refresh({ relayout: true })');
  return byId.get("graph-stats").textContent;
});
step("グルーピング切替（囲まない）", () => {
  byId.get("grouping").value = "none";
  probe('refresh({ relayout: true })');
  return byId.get("graph-stats").textContent;
});
step("整列レイアウトで重なりが無い", () => {
  byId.get("layout").value = "grid";
  probe("refresh({ relayout: true })");
  return probe(`(() => {
    const ids = visibleGraph().nodes.map((n) => n.id).filter((id) => positions.has(id));
    let overlaps = 0;
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const a = positions.get(ids[i]);
        const b = positions.get(ids[j]);
        if (Math.abs(a.x - b.x) < NODE_W && Math.abs(a.y - b.y) < NODE_H) overlaps += 1;
      }
    }
    if (overlaps) throw new Error(overlaps + " 組が重なっています");
    return ids.length + " ノード、重なり 0";
  })()`);
});
step("力学レイアウトでも重なりが無い", () => {
  byId.get("layout").value = "force";
  probe("refresh({ relayout: true })");
  const result = probe(`(() => {
    const ids = visibleGraph().nodes.map((n) => n.id).filter((id) => positions.has(id));
    let worst = 0;
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const a = positions.get(ids[i]);
        const b = positions.get(ids[j]);
        if (Math.abs(a.x - b.x) < NODE_W && Math.abs(a.y - b.y) < NODE_H) worst += 1;
      }
    }
    return worst;
  })()`);
  if (result > 0) throw new Error(`${result} 組が重なっています`);
  byId.get("layout").value = "grid";
  probe("refresh({ relayout: true })");
  return "重なり 0";
});
step("追加フォーム（ノード）", () => { probe('openAddForm("node")'); return byId.get("inspector").innerHTML.length; });
step("追加フォーム（エッジ）", () => { probe('openAddForm("edge")'); return byId.get("inspector").innerHTML.length; });
step("追加フォーム（ハイパーノード）", () => { probe('openAddForm("hypernode")'); return byId.get("inspector").innerHTML.length; });
step("エッジの接続先を選べる", () => {
  const html = probe('(() => { const e = doc.edges[0]; select("edge", e.id); return document.getElementById("inspector").innerHTML; })()');
  if (!html.includes('data-key="source"') || !html.includes('data-key="target"')) {
    throw new Error("始点・終点の選択欄がありません");
  }
  return "始点・終点の選択欄あり";
});
step("メンバー選択フォーム", () => {
  probe('openMembersForm(doc.hypernodes.find((h) => h.kind === "layer"))');
  return byId.get("inspector").innerHTML.length;
});
step("全体表示", () => { probe("fitToScreen()"); return probe("JSON.stringify(view)"); });
step("YAML書き出し", () => probe('toYaml(doc.toDocument(), 0).length'));
step("書き出しパネル表示", () => {
  probe("exportYaml()");
  const panel = byId.get("export-panel");
  const text = byId.get("export-text").value;
  if (panel.hidden !== false) throw new Error("パネルが開いていません");
  if (!text.includes("hypernodes:")) throw new Error("YAML本文が入っていません");
  return text.length;
});
step("検証", () => probe("JSON.stringify(doc.validate())"));

process.stdout.write(JSON.stringify(results, null, 1));
