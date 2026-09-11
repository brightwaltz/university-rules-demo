"""グラフ文書（node / edge / hypernode）の読み込みと検証。

``data/ontology_graph.yaml`` を読み、参照整合性と入れ子の健全性を確かめたうえで、
Python 側（src/ontology.py）と画面側（docs/graph.html）の両方へ同じ構造を渡す。

hypernode は「メンバーを持つノード」である。ノードとして edge の端点になれる一方、
内部に部分グラフを抱える。用途は2つ。

* 階層構造 … ``members`` に node / hypernode を入れて入れ子にする
* メタ知識 … ``about`` に対象を列挙し、その集合についての言明（``statement``）を持つ

導出できる関係は文書に書かない。ルール種別の ``subject_class`` / ``rule_terms`` /
``evaluated_terms`` と、用語の ``close_match`` からは、読み込み時にエッジを生成する
（``derived=True``）。二重管理を避けるためで、画面側でも導出エッジは編集できない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field


class GraphDocumentError(ValueError):
    """グラフ文書の構造がおかしいことを示す。"""


NODE_KINDS = {"concept", "term", "rule-type", "vocabulary"}
HYPERNODE_KINDS = {"layer", "domain", "rule-group", "rationale"}
EDGE_KINDS = {
    "uses",
    "alternative",
    "close-match",
    "derived-from",
    "defined-in",
    "has-property",
    "range",
    "reuses",
    "constrains",
    "sets",
    "reads",
    "about",
}
TERM_KINDS = {"class", "property"}


class Namespace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    iri: str
    label: str
    role: str = "support"
    version: str | None = None
    note: str | None = None


class Node(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    kind: str
    label: str


class HyperNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    label: str
    members: list[str] = Field(default_factory=list)
    about: list[str] = Field(default_factory=list)
    statement: str | None = None
    evidence: str | None = None
    note: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    kind: str
    label: str | None = None
    derived: bool = False


class GraphDocument:
    """読み込み済みのグラフ文書。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise GraphDocumentError("グラフ文書の形式が正しくありません。")

        self.meta: dict[str, Any] = dict(raw.get("meta") or {})
        self.namespaces = [Namespace.model_validate(item) for item in raw.get("namespaces") or []]
        self.nodes = [Node.model_validate(item) for item in raw.get("nodes") or []]
        self.hypernodes = [HyperNode.model_validate(item) for item in raw.get("hypernodes") or []]
        self.explicit_edges = [
            Edge.model_validate({"id": self._edge_id(item), **item})
            for item in raw.get("edges") or []
        ]

        self._by_id: dict[str, Node | HyperNode] = {}
        for item in [*self.nodes, *self.hypernodes]:
            if item.id in self._by_id:
                raise GraphDocumentError(f"id が重複しています: {item.id}")
            self._by_id[item.id] = item

        self._terms_by_curie: dict[str, Node] = {}
        for node in self.nodes:
            curie = getattr(node, "curie", None)
            if node.kind == "term":
                if not curie:
                    raise GraphDocumentError(f"term ノードに curie がありません: {node.id}")
                if curie in self._terms_by_curie:
                    raise GraphDocumentError(f"CURIE が重複しています: {curie}")
                self._terms_by_curie[curie] = node

        # ノード単体の検証は導出より先に行う。順序を逆にすると、接頭辞の誤りが
        # 「参照先の用語がない」という分かりにくいエラーになる。
        self._validate_nodes()
        self.derived_edges = self._derive_edges()
        self.edges = [*self.explicit_edges, *self.derived_edges]
        self._validate_relations()

    # --- 構築 -------------------------------------------------------------

    @staticmethod
    def _edge_id(item: dict[str, Any]) -> str:
        if item.get("id"):
            return str(item["id"])
        return "edge/{kind}/{source}->{target}".format(
            kind=item.get("kind", "?"), source=item.get("source", "?"), target=item.get("target", "?")
        )

    def _term_id(self, curie: str) -> str | None:
        node = self._terms_by_curie.get(curie)
        return node.id if node else None

    def _derive_edges(self) -> list[Edge]:
        """文書に書かれていないが、属性から一意に決まるエッジを作る。"""
        edges: list[Edge] = []

        def add(source: str, curie: str, kind: str, label: str | None = None) -> None:
            target = self._term_id(curie)
            if target is None:
                raise GraphDocumentError(
                    f"{source} が参照する用語ノードがありません: {curie}"
                )
            edges.append(
                Edge(
                    id=f"edge/{kind}/{source}->{target}",
                    source=source,
                    target=target,
                    kind=kind,
                    label=label,
                    derived=True,
                )
            )

        for node in self.nodes:
            if node.kind == "rule-type":
                subject_class = getattr(node, "subject_class", None)
                if not subject_class:
                    raise GraphDocumentError(f"rule-type に subject_class がありません: {node.id}")
                add(node.id, subject_class, "constrains", "制約するクラス")
                for yaml_key, curie in (getattr(node, "rule_terms", None) or {}).items():
                    add(node.id, curie, "sets", yaml_key)
                for curie in getattr(node, "evaluated_terms", None) or []:
                    add(node.id, curie, "reads")
            if node.kind == "term":
                for curie in getattr(node, "close_match", None) or []:
                    add(node.id, curie, "close-match", "skos:closeMatch")
        return edges

    # --- 検証 -------------------------------------------------------------

    def _validate_nodes(self) -> None:
        prefixes = {namespace.prefix for namespace in self.namespaces}
        if len(prefixes) != len(self.namespaces):
            raise GraphDocumentError("接頭辞が重複しています。")

        for node in self.nodes:
            if node.kind not in NODE_KINDS:
                raise GraphDocumentError(f"未知の node kind です: {node.kind}（{node.id}）")
            curie = getattr(node, "curie", None)
            if curie:
                prefix = str(curie).split(":", 1)[0]
                if prefix not in prefixes:
                    raise GraphDocumentError(f"未登録の接頭辞です: {curie}")
                term_kind = getattr(node, "term_kind", None)
                if term_kind not in TERM_KINDS:
                    raise GraphDocumentError(f"term_kind が不正です: {node.id}")

    def _validate_relations(self) -> None:
        for hypernode in self.hypernodes:
            if hypernode.kind not in HYPERNODE_KINDS:
                raise GraphDocumentError(
                    f"未知の hypernode kind です: {hypernode.kind}（{hypernode.id}）"
                )
            for member in hypernode.members:
                if member not in self._by_id:
                    raise GraphDocumentError(
                        f"{hypernode.id} のメンバーが存在しません: {member}"
                    )
            for target in hypernode.about:
                if target not in self._by_id and target not in {edge.id for edge in self.edges}:
                    raise GraphDocumentError(
                        f"{hypernode.id} の about が存在しません: {target}"
                    )
            if hypernode.kind == "rationale" and not hypernode.statement:
                raise GraphDocumentError(f"rationale には statement が必要です: {hypernode.id}")

        for edge in self.edges:
            if edge.kind not in EDGE_KINDS:
                raise GraphDocumentError(f"未知の edge kind です: {edge.kind}（{edge.id}）")
            for endpoint in (edge.source, edge.target):
                if endpoint not in self._by_id:
                    raise GraphDocumentError(f"エッジの端点が存在しません: {endpoint}")

        self._check_member_cycles()

    def _check_member_cycles(self) -> None:
        """hypernode の入れ子に循環が無いことを確かめる。"""
        members = {h.id: [m for m in h.members if m in self._hypernode_ids()] for h in self.hypernodes}
        state: dict[str, int] = {}

        def walk(node_id: str, trail: list[str]) -> None:
            if state.get(node_id) == 1:
                raise GraphDocumentError(
                    "hypernode の入れ子が循環しています: " + " → ".join([*trail, node_id])
                )
            if state.get(node_id) == 2:
                return
            state[node_id] = 1
            for child in members.get(node_id, []):
                walk(child, [*trail, node_id])
            state[node_id] = 2

        for hypernode in self.hypernodes:
            walk(hypernode.id, [])

    def _hypernode_ids(self) -> set[str]:
        return {hypernode.id for hypernode in self.hypernodes}

    # --- 参照 -------------------------------------------------------------

    def get(self, node_id: str) -> Node | HyperNode:
        try:
            return self._by_id[node_id]
        except KeyError as exc:
            raise GraphDocumentError(f"ノードが見つかりません: {node_id}") from exc

    def term(self, curie: str) -> Node:
        try:
            return self._terms_by_curie[curie]
        except KeyError as exc:
            raise GraphDocumentError(f"用語が見つかりません: {curie}") from exc

    def has_term(self, curie: str) -> bool:
        return curie in self._terms_by_curie

    def terms(self) -> list[Node]:
        return list(self._terms_by_curie.values())

    def nodes_of_kind(self, kind: str) -> list[Node]:
        return [node for node in self.nodes if node.kind == kind]

    def hypernodes_of_kind(self, kind: str) -> list[HyperNode]:
        return [hypernode for hypernode in self.hypernodes if hypernode.kind == kind]

    def parents_of(self, node_id: str) -> list[str]:
        return [h.id for h in self.hypernodes if node_id in h.members]

    def descendants(self, hypernode_id: str) -> list[str]:
        """入れ子をたどって、含まれる全メンバーを返す。"""
        seen: list[str] = []
        stack = list(self.get(hypernode_id).members)  # type: ignore[union-attr]
        hypernode_ids = self._hypernode_ids()
        while stack:
            current = stack.pop(0)
            if current in seen:
                continue
            seen.append(current)
            if current in hypernode_ids:
                stack.extend(self.get(current).members)  # type: ignore[union-attr]
        return seen

    def edges_of(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id or e.target == node_id]

    # --- 射影 -------------------------------------------------------------

    def context(self) -> dict[str, str]:
        return {namespace.prefix: namespace.iri for namespace in self.namespaces}

    def crosswalk(self) -> list[dict[str, Any]]:
        """concept → term のエッジから語彙対応表を組み立てる。

        表は文書に書かない。uses / alternative のエッジから毎回導出する。
        """
        rows: list[dict[str, Any]] = []
        for concept in self.nodes_of_kind("concept"):
            used: list[str] = []
            alternatives: dict[str, list[str]] = {}
            for edge in self.edges:
                if edge.source != concept.id:
                    continue
                target = self._by_id.get(edge.target)
                curie = getattr(target, "curie", None)
                if not curie:
                    continue
                prefix = str(curie).split(":", 1)[0]
                if edge.kind == "uses":
                    used.append(str(curie))
                elif edge.kind == "alternative":
                    alternatives.setdefault(prefix, []).append(str(curie))
            if not used:
                continue
            rows.append(
                {
                    "concept": concept.label,
                    "concept_id": concept.id,
                    "demo": " ＋ ".join(used),
                    "schema_org": self._cell(used, alternatives, "schema"),
                    "ccso": self._cell(used, alternatives, "ccso"),
                    "oloud": self._cell(used, alternatives, "oloud"),
                }
            )
        return rows

    @staticmethod
    def _cell(used: list[str], alternatives: dict[str, list[str]], prefix: str) -> str:
        own = [curie for curie in used if curie.startswith(f"{prefix}:")]
        other = alternatives.get(prefix, [])
        if own:
            return " ＋ ".join(own)
        if other:
            return " / ".join(other)
        return "（該当なし）"

    def to_dict(self) -> dict[str, Any]:
        """画面へ渡すための素の辞書。導出エッジも含める。"""
        return {
            "meta": self.meta,
            "namespaces": [namespace.model_dump(exclude_none=True) for namespace in self.namespaces],
            "nodes": [node.model_dump(exclude_none=True) for node in self.nodes],
            "hypernodes": [h.model_dump(exclude_none=True) for h in self.hypernodes],
            "edges": [edge.model_dump(exclude_none=True) for edge in self.edges],
            "kinds": {
                "node": sorted(NODE_KINDS),
                "hypernode": sorted(HYPERNODE_KINDS),
                "edge": sorted(EDGE_KINDS),
            },
        }

    def to_yaml(self) -> str:
        """編集結果を書き戻せる形（導出エッジは落とす）。"""
        document = {
            "meta": self.meta,
            "namespaces": [n.model_dump(exclude_none=True) for n in self.namespaces],
            "hypernodes": [h.model_dump(exclude_none=True) for h in self.hypernodes],
            "nodes": [n.model_dump(exclude_none=True) for n in self.nodes],
            "edges": [
                {k: v for k, v in e.model_dump(exclude_none=True).items() if k != "derived"}
                for e in self.explicit_edges
            ],
        }
        return yaml.safe_dump(document, allow_unicode=True, sort_keys=False, default_flow_style=False)


def iter_curies(document: GraphDocument) -> Iterable[str]:
    for node in document.terms():
        yield str(getattr(node, "curie"))
