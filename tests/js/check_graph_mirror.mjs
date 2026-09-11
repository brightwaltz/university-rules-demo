// docs/graph.html のグラフ文書モデル（<script id="graph-engine">）を Node.js で実行し、
// 文書・導出エッジ・語彙対応表・検証結果・YAML書き出しを標準出力へ書き出す。
// tests/test_docs_mirror.py が src/graph_document.py の結果と突き合わせる。
//
//   node tests/js/check_graph_mirror.mjs <docs/graph.html>

import { readFileSync } from "node:fs";
import vm from "node:vm";

const [htmlPath] = process.argv.slice(2);
const html = readFileSync(htmlPath, "utf8");

const blocks = new Map();
for (const match of html.matchAll(/<script[^>]*\bid="([a-z-]+)"[^>]*>([\s\S]*?)<\/script>/g)) {
  blocks.set(match[1], match[2]);
}

const engine = blocks.get("graph-engine");
if (!engine) {
  console.error('docs/graph.html に <script id="graph-engine"> がありません。');
  process.exit(1);
}

globalThis.document = {
  getElementById: (id) => (blocks.has(id) ? { textContent: blocks.get(id) } : null),
};

const probe = `
;(() => {
  const doc = new GraphDoc(EMBEDDED);
  return {
    document: doc.toDocument(),
    derived_edge_ids: doc.derivedEdges().map((edge) => edge.id).sort(),
    crosswalk: doc.crosswalk(),
    problems: doc.validate(),
    roots: doc.roots().map((hyper) => hyper.id),
    descendants: Object.fromEntries(
      doc.hypernodes.map((hyper) => [hyper.id, doc.descendants(hyper.id).sort()])
    ),
    yaml: toYaml(doc.toDocument(), 0).replace(/^\\n/, ""),
  };
})()
`;

const output = vm.runInThisContext(engine + probe, { filename: "docs/graph.html#graph-engine" });
process.stdout.write(JSON.stringify(output, null, 1));
