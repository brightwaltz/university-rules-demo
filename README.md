# University Rules as Code Assistant

学生のパーソナルデータと大学規則を組み合わせ、履修・卒業に関する回答、個別通知、適格性判定を行う研究用MVPです。APIキーは不要で、大学制度上の判断に生成AIを使用しません。

このリポジトリは Claude Code（Anthropic）を使って作っています。

データは **schema.org を基底とする共通オントロジー上のナレッジグラフ**（`data/university_graph.jsonld`）として保持し、規則は **そのオントロジーの用語に束縛されたRules as Code**（`data/rules.yaml`）として保持します。

そのオントロジー自体も **node / edge / hypernode からなるグラフ文書**（`data/ontology_graph.yaml`）として持ち、[オントロジーグラフ画面](docs/graph.html)で可視化・編集できます。

判定は、規程ごとに書いた **Horn 節を、リポジトリに含まれる処理系で実際に評価して**決めます（Python 版は `src/horn.py`、Pages 版は同じ節を `docs/assets/horn.js` で評価）。Horn 節が読めない人のために、節と証明の**読み下し**と、**例題による確認**の仕組みを持ちます。

## このデモにおける Rules as Code

```text
自然言語の質問
    ↓
RuleBased Intent Detection
    ↓
ナレッジグラフ（schema.org ＋ CCSO ＋ ローカル拡張）＋ rules.yaml の値
    ↓ 三つ組を事実へ平らにする
Horn節処理系（層化否定と集約つきの Datalog）
    ↓ 規程ごとの Horn 節（ontology_graph.yaml）を評価
DecisionResult（回答・根拠条文・Horn節・証明木・読み下し・不足条件）
    ↓
回答画面 / パーソナライズ通知 / JSON-LD出力 / 例題チェック
```

登録ルールで扱えない質問には、推測せず「このプロトタイプに登録されているルールでは判定できません」と返します。

## オントロジー

### 何を、どの語彙で表しているか

schema.org は**科目カタログ・課程・規程・通知**の語彙を十分に持っています。一方で**学籍側**（誰がどの科目を修得したか、在籍しているか）の語彙をほとんど持ちません。`schema:numberOfCredits` の domain は `Course` と `EducationalOccupationalProgram` だけで、`Person` には付けられません。

そこで次の優先順位で語彙を選んでいます。

1. schema.org に適切な用語があれば、必ず schema.org を使う
2. schema.org に無く CCSO にある概念（在籍・修得・履修登録）は CCSO を使う
3. どちらにも無い概念だけ、ローカル拡張 `urd:` を定義し、近い既存用語を `skos:closeMatch` として明示する（同一視はしない）

| 概念 | 本デモ | schema.org | CCSO | OLOUD |
|---|---|---|---|---|
| 大学 | `schema:CollegeOrUniversity` | ✅ | `ccso:University` | （aiiso経由） |
| 学部 | `schema:EducationalOrganization` | ✅ | `ccso:School` | `aiiso:Department` |
| 課程 | `schema:EducationalOccupationalProgram` | ✅ | `ccso:ProgramofStudy` | `oloud:studyProgramme` |
| 科目 | `schema:Course` | ✅ | `ccso:Course` | `aiiso:Subject` / `aiiso:Course` |
| 単位数 | `schema:numberOfCredits` | ✅ | `ccso:creditsECTS` | `oloud:subjectCredit` |
| 科目コード | `schema:courseCode` | ✅ | `ccso:code` | `aiiso:code` |
| 学生 | `schema:Person` ＋ `ccso:UndergraduateStudent` | ✅ | `ccso:Student` | `foaf:Person` |
| 在籍 | `ccso:enrolledIn` | — | ✅ | — |
| 修得済み科目 | `ccso:hasCompleted` | — | ✅ | — |
| 履修登録済み科目 | `ccso:hasRegistered` | — | ✅ | — |
| 履修登録という行為と状態 | `schema:RegisterAction` ＋ `schema:actionStatus` | ✅ | — | — |
| 履修登録期限 | `schema:applicationDeadline` | ✅ | — | — |
| 学位 | `schema:EducationalOccupationalCredential` | ✅ | `ccso:Bachelor` | `oloud:degree` |
| 学則・履修規程 | `schema:Legislation` | ✅ | — | — |
| 通知 | `schema:Message` | ✅ | — | — |
| 学年 | `urd:yearOfStudy` | — | — | — |
| 入学区分 | `urd:admissionCategory`（値は `schema:DefinedTerm`） | — | — | — |
| 修得単位数の合計 | `urd:earnedCredits` | — | — | — |
| 単位修得の時期 | `schema:AchieveAction` ＋ `urd:atYearOfStudy` | ✅ | — | — |
| 年間履修上限 | `urd:annualCreditLimit` | — | — | — |
| 卒業必修科目 | `urd:requiredCourse` | `schema:programPrerequisites`（closeMatch） | `ccso:hasPrerequisite`（closeMatch） | `oloud:subjectRequires`（closeMatch） |

この README の表は手で書いています（OLOUD 側の `aiiso:` など、グラフ文書に無い対応も載せています）。画面の「5. オントロジーとナレッジグラフ」の対応表は手で保守せず、`data/ontology_graph.yaml` の concept → term エッジから毎回導出しています（後述の[オントロジーグラフ](#オントロジーグラフグラフ文書)）。用語を足したときは、この表も手で直してください。

### OLOUD と CCSO について（事実確認）

- **CCSO**（Curriculum Course Syllabus Ontology, Evangelos Katis）の名前空間は `https://w3id.org/ccso/ccso#`。配布されている `ccso.owl` を確認すると、CCSO 自身が `http://schema.org/Organization` と `http://schema.org/EducationalOrganization` を直接再利用しており、FOAF と併用されています。**schema.org と組み合わせて使える設計**です。
- **OLOUD**（Ontology for Linked Open University Data, Fleiner・Szász・Micsik / Óbuda University）の名前空間は `http://lod.nik.uni-obuda.hu/oloud/oloud#`（時間モジュールは `otime`）。こちらは **AIISO / FOAF / Dublin Core の上に構築されており、schema.org は使っていません**。
- どちらも **schema.org の一部ではありません**。schema.org 側にこれらが収録されているわけではなく、大学領域の独立した語彙です。本デモは共通の土台として schema.org を採り、CCSO を学籍側の補完として組み合わせ、OLOUD については対応関係を表に示すに留めています（原論文に明記された用語のみ記載）。

なお、本作業時点で OLOUD の配布サーバー（`lod.nik.uni-obuda.hu`）は応答しませんでした。OLOUD の用語は Acta Polytechnica Hungarica 14(4), 2017 の記述に基づいています。

### ローカル拡張の名前空間

| 接頭辞 | IRI | 用途 |
|---|---|---|
| `urd:` | `https://brightwaltz.github.io/university-rules-demo/ns#` | 語彙（用語の定義） |
| `urdi:` | `https://brightwaltz.github.io/university-rules-demo/id/` | インスタンス（学生・科目・条文などのノード） |

語彙とデータで名前空間を分けているため、接頭辞を見るだけで「用語」か「ノード」かを判別できます。`urd:` の各用語の定義は `src/ontology.py` の `LOCAL_TERM_DEFINITIONS` にあります。

### 用語の実在検証

`data/schemaorg_terms.json`（schema.org 30.0 / 2026-03-19 の公式配布物から抽出）と `data/ccso_terms.json`（`ccso.owl` から抽出）に用語名を固定しており、`tests/test_ontology.py` が次を検証します。

- Rule Engine が使う全CURIEが、実在する用語であること
- ナレッジグラフに現れる全CURIEが、実在する用語であること
- `urd:` の用語がすべて定義付きで文書化されていること
- `skos:closeMatch` の参照先が実在する用語であること

存在しない用語を書いてしまう事故は、テストで落ちます。

## オントロジーグラフ（グラフ文書）

語彙の構造そのものを `data/ontology_graph.yaml` に **グラフ文書**として持ちます。表示用の複製ではなく、`src/ontology.py` がここから語彙定義を読み込むため、編集すると判定・検証・画面表示のすべてに反映されます。

### 3つの構成要素

| 要素 | 役割 | 主なキー |
|---|---|---|
| `nodes` | 単一の対象。概念（concept）／語彙の用語（term）／ルール種別（rule-type）／語彙そのもの（vocabulary） | `id, kind, label, curie, term_kind, comment, close_match` |
| `edge_kinds` | 関係の種類そのものの定義。名前・説明・読み下し文 | `id, label, description, reading` |
| `edges` | 有向の関係。端点には hypernode も取れる | `source, target, kind, label` |
| `hypernodes` | メンバーを持つノード。入れ子にできる | `id, kind, label, members, about, statement, evidence` |

**エッジの意味は種類が持つ。** `edges` 側は `kind` で種類の id を指すだけにして、意味の説明を1箇所に集めています。各種類は読み下し文の型（`reading`）を持ち、そこへ端点の呼び名を差し込むことで、**どのエッジも日本語の一文になります**。

```yaml
- id: derived-from
  label: から導出する
  description: その用語の値が、別の用語をたどって計算されることを示す。
  reading: "{source} は {target} をたどって導出する"
```

```text
urd:earnedCredits は ccso:hasCompleted をたどって導出する（修得科目をたどって）
科目 は schema:Course で表す
urd:requiredCourse は schema:programPrerequisites に意味が近い（同一ではない）
```

用語は CURIE で示します。概念「科目」と `schema:Course` の和名「科目」のように日本語名が重なることがあり、ラベルのままでは「科目 は 科目 で表す」という読めない文になるためです。各エッジの `label` は、その1本だけに固有の補足として文末へ括弧書きで添えられます。定義に無い種類は読み込み時に弾かれます。

**hypernode** は「ノードでありながら部分グラフを内包するもの」で、用途が2つあります。

1. **階層構造** — `members` に node / hypernode を入れて入れ子にする。
   例：`layer/vocabulary` ⊃ `layer/schema-org` ⊃ `term/schema:Course`
2. **メタ知識** — `about` に対象を列挙し、その集合についての言明（`statement`）と根拠（`evidence`）を持つ。
   例：`rationale/why-ccso` が「なぜ学籍側を CCSO で補うのか」を、学籍領域と関連用語に対して述べる。

hypernode 自身も edge の端点になれます（例：`vocabulary/ccso --defined-in--> layer/ccso`）。

### AI にも人にも扱いやすくするための約束

- **id は安定させる。** 名称変更は `label` を変える。id を変えると参照が壊れます。
- **1つの事実は1箇所にだけ書く。** 導出できる関係は `edges` に書きません。ルール種別の `subject_class` / `rule_terms` / `evaluated_terms` と、用語の `close_match` からはエッジが自動生成されます（画面上では鍵アイコン付きで編集不可）。
- **語彙対応表も導出。** concept → term の `uses` / `alternative` エッジから毎回組み立てるので、表を手で保守しません。
- **kind は閉じた集合から選ぶ。** 増やすときは `src/graph_document.py` も更新します。
- **壊れた編集は読み込み時に落ちる。** 参照整合性、未登録の接頭辞、`kind` の誤り、id の重複、hypernode の入れ子の循環、`close_match` の相手不在を検査します（`tests/test_graph_document.py`）。

### 画面で可視化・編集する

`docs/graph.html`（[オントロジーグラフ](docs/graph.html)）で操作できます。

**見る**

- **整列レイアウト（既定）** — ハイパーノードごとに区画を作り、その中へ格子状に並べます。構成上ノードが重ならないので、100件規模でも一つずつ読めます。
- **力学レイアウト** — 関係の近さを見たいとき用。配置後に矩形の重なりを解消する処理を入れてあるので、こちらも重なりません。
- ノードをクリックすると、**関係する分だけを濃く**描き、その関係のラベルだけを表示します（100件規模では、これが無いとどの線がどこへ向かうか追えません）。ツールバーで切り替えられます。
- ハイパーノードは囲みとして描画。ダブルクリックで折りたたみ／展開、左ペインの階層ツリーからも辿れます。
- メタ知識（rationale）は 🛈 付きの注釈ノードとして描かれ、`about` の対象へ点線でつながります。
- 語彙レイヤ／業務領域／ルール種別で囲みを切り替え、導出エッジやメタ知識の表示も切り替えられます。

**直す**

| 対象 | できること |
|---|---|
| ノード | ラベル・説明・備考の編集、削除 |
| エッジ | **始点と終点の付け替え**、関係の種類、固有の補足の編集、削除。選ぶと意味の文と種類の説明が出る |
| ハイパーノード | ラベル・言明・根拠の編集、**メンバー／対象の選び直し**、削除 |
| 追加 | ノード／エッジ／ハイパーノードをフォームから追加 |

導出エッジ（鍵アイコン）は編集できません。元になっている `rule_terms` や `close_match` を直すと変わります。削除しようとすると、巻き込まれる関係の本数を先に知らせます。

編集は閲覧中のブラウザの `localStorage` に保存され、**同じ形式の YAML として書き出せます**。書き出したファイルを `data/ontology_graph.yaml` へ置き、`python scripts/sync_docs_data.py && python -m pytest` を実行すればリポジトリへ取り込めます。

書き出した YAML が実際に読み戻せることは `tests/test_docs_mirror.py` が検証しています（Node.js で画面のモデル層を実行し、Python の読み込み結果と突き合わせ）。

## ナレッジグラフ

`data/university_graph.jsonld` に、大学・学部・課程・学位・科目カタログ・規程（条文単位）・学生・履修登録行為・修得記録を1つのJSON-LD文書として持ちます。

重要な点として、**学生の単位数はグラフに書かれていません**。`ccso:hasCompleted` / `ccso:hasRegistered` がつなぐ科目をたどり、`schema:numberOfCredits` を合計して導出します。

```json
{
  "@id": "urdi:student/S001",
  "@type": ["schema:Person", "ccso:UndergraduateStudent"],
  "schema:identifier": "S001",
  "schema:name": "山田太郎",
  "schema:affiliation": { "@id": "urdi:organization/engineering" },
  "ccso:enrolledIn": { "@id": "urdi:program/engineering-2026" },
  "urd:yearOfStudy": 3,
  "ccso:hasCompleted": [{ "@id": "urdi:course/REQ-A" }, "…"],
  "ccso:hasRegistered": [{ "@id": "urdi:course/FND-13" }, "…"]
}
```

履修登録が済んでいるかどうかは、学生の属性ではなく**行為の状態**として持ちます。

```json
{
  "@id": "urdi:action/S001/registration-2026-first",
  "@type": "schema:RegisterAction",
  "schema:agent": { "@id": "urdi:student/S001" },
  "schema:object": { "@id": "urdi:program/engineering-2026" },
  "schema:actionStatus": { "@id": "schema:PotentialActionStatus" }
}
```

単位を**いつ**修得したかも、行為として持ちます。学生×年度ごとに1つの `schema:AchieveAction` を置き、その時点の学年を `urd:atYearOfStudy` で示します。「第1年次において修得した単位」のような規程は、これが無いと書けません。

```json
{
  "@id": "urdi:achievement/S002/2023",
  "@type": "schema:AchieveAction",
  "schema:name": "2023年度（1年次）の単位修得",
  "schema:agent": { "@id": "urdi:student/S002" },
  "schema:object": [{ "@id": "urdi:course/GEN-01" }, "…"],
  "schema:actionStatus": { "@id": "schema:CompletedActionStatus" },
  "ccso:academicYear": "2023",
  "urd:atYearOfStudy": 1
}
```

`ccso:hasCompleted` は相互運用のための要約として残しています。両者が食い違わないこと（修得記録の科目の和集合が `ccso:hasCompleted` と一致し、同じ科目が2つの記録に現れないこと）は、修得記録を持つ学生について読み込み時に検証します。

条文ノード（`schema:Legislation`）には本文を `schema:text` で持たせています。画面では「規程の本文 → Horn 節 → 読み下し」を並べて示し、AI が例題を作るときの入力にもなります。

`KnowledgeGraph.students()` がこのグラフを内部モデル `Student` へ射影し、画面表示や通知に使います。判定のほうは、グラフに `rules.yaml` の値を重ねた実効グラフを事実へ平らにし（`FactBase.from_jsonld`）、その上で Horn 節を評価します。外部データ源を差し替えるときは、グラフの読み込みと射影だけを書き換えます。

## ルールとオントロジーの接続

`data/rules.yaml` の各ルールは、次の3点でオントロジーへ接続します。

```yaml
- rule_id: RULE-GRAD-001
  rule_type: graduation_credit_requirement
  title: 卒業必要単位数
  source: 2026年度学則 第32条第1項
  subject: urdi:program/engineering-2026            # 制約する対象ノード
  legislation: urdi:legislation/gakusoku-2026/art32-1  # 根拠条文（schema:Legislation）
  required_credits: 124                             # → schema:numberOfCredits
```

- 値のキーと用語の対応は `src/ontology.py` の `RULE_TYPE_ONTOLOGY` が持ちます。
- `subject` は、そのルール種別が制約すべきクラス（例：`schema:EducationalOccupationalProgram`）であることを保存前に検証します。
- `legislation` は、グラフ上に存在する条文ノードであることを保存前に検証します。
- 必修科目は、グラフの科目カタログに存在する科目コードだけを指定できます。

規則値をグラフ本体には書きません。`rules.yaml` が唯一の情報源で、外部へ書き出すときと判定するときだけ、対象ノードへ重ねた「実効グラフ」として1文書にまとめます（`src/jsonld_export.py` の `effective_graph`）。

## Horn節インタープリタ

判定は、ルール種別ごとに書いた Horn 節を**実際に評価して**決めています（`src/horn.py`）。以前の版では Horn 節は表示用の文字列で、判定は Python の条件式が行っていましたが、今は節そのものが実行されます。Pages 版も同じ節を、JavaScript へ移植した同じ処理系（`docs/assets/horn.js`）で評価します。ただし、日付の演算を使う「履修登録期限」と、条件がルールごとに変わる「個別通知ルール」の2種別は、Horn 節を表示用に持つだけで処理系には渡していません（グラフ文書で `executable: false`）。

### 扱える言語

Prolog の部分集合で、論理プログラミングの分類では **層化否定と集約つきの Datalog** にあたります。探索は Prolog と同じ深さ優先・左から右・上から下です。

| 構成要素 | 書き方 | 用途 |
|---|---|---|
| 事実 | `ccso:hasCompleted(Student, Course)` | ナレッジグラフの三つ組そのもの |
| 規則（Horn 節） | `頭部 :- 本体1, 本体2.` | 条件の連言 |
| 選言 | 同じ頭部の節を複数書く | 「AまたはB」 |
| 比較・算術 | `Earned >= Minimum`, `M is ceiling(R * P / 100)`。比較は `>=` `>` `=<` `<` `=` `\=`、関数は `max` `min` `ceiling` `floor` `round` `abs` | しきい値、割合 |
| 否定 | `\+ G` または `not(G)` | 例外・但し書き |
| 全称 | `forall(C, A)` | 「必修科目をすべて」 |
| 集約 | `aggregate_all(sum(X), G, Total)`。ほかに `count` `max(X)` `min(X)` | 単位数の合計、件数 |

読み込み時に次を検査し、違反があれば起動時に落とします。

- **安全性**：頭部の変数は、本体のトップレベルの肯定アトム・`is` の左辺・`=` の両辺・集約の結果のいずれかで束縛されること（`forall` や否定、集約のゴールの内側だけに現れる変数は認めない。そのため `thesis_prerequisites_ok` には `rdf:type(Student, schema:Person)` と `rdf:type(Thesis, schema:Course)`、`urd:earnedCredits` の定義節には `rdf:type(Student, schema:Person)` の条件を置いている）
- **層化**：否定・`forall`・集約を経由した循環がないこと（循環があると意味が定まらない）
- **定義の一意性**：1つの述語は1箇所（1つのルール種別か1つの用語）でだけ定義すること

### ナレッジグラフをどう事実にしているか（単一化の範囲）

ナレッジグラフは三つ組 `述語(主語, 目的語)` の集合に平らにして渡します。各事実の引数は IRI かリテラルで、関数記号（入れ子の項）はありません。

```prolog
rdf:type(urdi:student/S001, schema:Person).
ccso:hasCompleted(urdi:student/S001, urdi:course/REQ-A).
schema:numberOfCredits(urdi:course/REQ-A, 4).
```

したがって Horn 節の変数（`Student`、`Program` など）に入るのは**ノードの識別子**であって、**部分グラフではありません**。単一化は定数どうしの照合だけになり、停止性が保証されます。

研究会で指摘された「2つのナレッジグラフの単一化」は、この制限によって**扱わないことにしています**。どの属性どうしを対応させるか、多値属性のなかのどの値とどの変数を対応させるかが一意に決まらず、組み合わせが爆発する問題です。本デモでは、多値属性（`ccso:hasCompleted` は科目を何件も持つ）を値ごとの事実に分け、変数は1件ずつ値を取ってバックトラックで列挙します。代わりに、グラフ構造そのものの照合（「この学生の履修パターンは、あのモデルコースと同じ形か」など）は書けません。その領域は、グラフを項として扱う推論エンジンの課題として切り分けています（次の小節）。

### 橋田先生の推論エンジン（KG-WAM）との関係

研究会で紹介された推論エンジンは、[assemblogue/plrDart](https://github.com/assemblogue/plrDart) の `plr_wam`（処理系 KG-WAM、言語 KGLog）です。以下は 2026-10-10 時点の設計書を読んだ範囲の理解で、コードは実行していません。

KG-WAM は、知識グラフのノードを名前つき引数の項（素性構造に近いもの）として扱い、平らにせず入れ子のまま単一化します。設計書は、研究会で指摘された2つの組み合わせ爆発、つまり属性どうしの対応づけと、多値属性の中の変数と定数の対応づけを明示しています。前者は運用上の安全条件で、後者は対応づけを先送りしたうえで列挙が起きないように節を書く運用で扱う方針です。担当者・権限・承認といった業務フローの概念を言語に含むことも特徴です。

本デモは、その設計書が採らないとしている「入れ子のノードを平らな事実に書き下す」側の、さらに制限した版にあたります。変数がノードの識別子しか取らないので、上の2つの問題はそもそも起きません。代わりに、構造そのものの照合は書けません。

将来つなぐなら、本デモで導いた関係を本体の無い Horn 節（事実）として KG-WAM に渡すか、KG-WAM 側から問い合わせる形が考えられます。その際は、集約と否定の意味の違い（本デモは層化した不動点で計算し、KG-WAM は組み込み述語と Else リンクで扱う）を境界でそろえる必要があります。

### 1つの規程に、Horn 節は1つではない

| ルール種別 | 節の数 | 構成 |
|---|---|---|
| 卒業研究の履修条件 | 4 | 入口1＋条件3（学年・単位・前提科目） |
| 1年次の修得単位要件 | 4 | 入口1＋修得単位の集計＋必要単位の計算＋修得記録の参照 |
| 卒業判定 | 3 | 入口1＋単位の要件（第32条第1項）＋必修の要件（同第2項）。別々のルール種別の節を組み合わせる |
| 修得単位数（用語の定義） | 1 | 集約 |

複雑な規程も、**頭部の変数だけを共有する小さな条件の連言**に分けて書いています。小さい節はそれぞれ単独で検証でき、判定結果では条件ごとに「満たす／満たさない」を示せます。

判定結果（`DecisionResult`）には、Horn 節の本体を実際の値で具体化した `premises` が入ります。各前提は「使った用語（CURIE）／対象ノード／実際の値／比較演算子／要求値／充足したか」を持つため、どの原子論理式が失敗して不許可になったのかを1件ずつ追えます。

判定結果には、入口の述語の**証明木**（`proof`）も入ります。成功した判定は使った節と事実をたどれ、失敗した判定は「最も先まで進めた節のどの条件で止まったか」を示します。

## Horn節で書けるもの・書けないもの

| 規程の形 | 例 | Horn 節で書けるか | 本デモでの扱い |
|---|---|---|---|
| 条件の連言 | 4年次以上、かつ100単位以上、かつ必修A・B | 書ける | 卒業研究の履修条件 |
| 選言 | 必修Aまたは代替科目Xを修得 | 書ける（節を複数書く） | — |
| 全称 | 必修科目をすべて修得 | 純粋な Horn 節では書けない。否定を足せば書ける（`forall(C, A)` ＝「C なのに A でないものはない」） | 卒業必修科目 |
| 合計・割合 | 第1年次に、卒業に必要な単位数の4分の1以上 | 純粋な Horn 節では書けない。集約を足せば書ける | **1年次の修得単位要件（新規）** |
| 時期 | 「第1年次において」 | 事実側に時期があれば書ける（データの持ち方の問題） | 修得記録 `schema:AchieveAction` に `urd:atYearOfStudy` |
| 例外・但し書き | ただし編入学生を除く | 否定で書ける | 1年次の修得単位要件の適用条件 `first_year_requirement_applies`（`\+ urd:admissionCategory(Student, urdi:term/transfer-admission)`）。例題 FY-AI-02 から足した |
| 裁量 | 教授会が特に認めた場合 | **書けない**（真偽を機械が決められない） | 人の判断を事実として入力する。無ければ「判定できません」 |
| 手続き・順序 | 申請し、審査を経て、承認される | 各段階の状態を事実（`schema:actionStatus`）として持てば、段階ごとの条件は書ける。手続きの実行そのものは別の仕組み | 履修登録の完了状態 |
| 推薦・類似度 | どの科目を取るべきか | 規則ではない | 対象外。判定結果を入力として使う側 |

研究会で例に出た「1年次で、卒業に必要な単位の25%を取らなければならない」は、類似度の問題ではなく**集約の問題**です。集約と、修得の時期を持つデータがあれば、Horn 節（の拡張）で書けます。

## 複雑なルールの例：1年次の修得単位要件

研究会で例に挙がった「1年次で、卒業に必要な単位の25%を取らなければならない」を、架空の履修規程 第14条として実装しました。

**規程の本文**（`schema:text`）

> 学生は、第1年次において、卒業に必要な単位数の4分の1以上を修得しなければならない。2 前項に定める単位数を修得できなかった学生は、修学指導の対象とする。

**ルールの値**（`data/rules.yaml`）

```yaml
- rule_id: RULE-FIRSTYEAR-001
  rule_type: first_year_credit_requirement
  subject: urdi:program/engineering-2026
  legislation: urdi:legislation/risyu-kitei-2026/art14
  year_of_study: 1             # → urd:targetYearOfStudy
  minimum_ratio_percent: 25    # → urd:minimumCreditRatio
```

卒業に必要な単位数（124）は、別のルール RULE-GRAD-001 の値をそのまま参照します。ルールどうしの組み合わせです。

**Horn 節**（`data/ontology_graph.yaml` の `rule-type/first_year_credit_requirement`）

```prolog
% 第14条の適用条件：編入学生は対象外（本学での第1年次がないため）
first_year_requirement_applies(Student) :-
    ccso:enrolledIn(Student, Program),
    \+ urd:admissionCategory(Student, urdi:term/transfer-admission).

% 履修規程 第14条：第1年次に、卒業に必要な単位数の4分の1以上を修得する
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
```

入口の節は3つの条件の連言で、各条件はさらに小さな節に分かれています。集約（`aggregate_all`）と算術（`ceiling`）を使うので純粋な Horn 節ではありませんが、層化されていて意味は一意に定まります。

判定では、先に適用条件 `first_year_requirement_applies` を問い合わせます。条文には書かれていない「編入学生は対象外」を、例題 FY-AI-02（3年次に編入学）の確認から足した節です。成り立たない学生（入学区分 `urd:admissionCategory` が編入学）には、満たす・満たさないを言わず「編入学のため、履修規程第14条（第1年次の修得単位）の対象外です。」（`unknown`）と答え、証明木は適用条件の節のものを返します。入学区分は学生ノードに `schema:DefinedTerm`（一般入学 `urdi:term/regular-admission`／編入学 `urdi:term/transfer-admission`）で持たせています。

**読み下し**（一般形）

```text
〈学生〉に履修規程第14条が適用される のは、次のすべてが成り立つとき
  1. 〈学生〉は〈課程〉に在籍している
  2. 〈学生〉の入学区分は編入学、ということはない

〈学生〉が、1年次の修得単位要件を満たしている のは、次のすべてが成り立つとき
  1. 〈学生〉が1年次に修得した単位数は〈修得単位数〉
  2. 〈学生〉が1年次に修得すべき単位数は〈必要単位数〉
  3. 〈修得単位数〉が〈必要単位数〉以上
```

**証明木**（S002 鈴木花子。1年次の修得は28単位）

```text
❌ 鈴木花子が、1年次の修得単位要件を満たしている
   ✅ 鈴木花子が1年次に修得した単位数は28
      ✅ 鈴木花子は工学部 学士課程（2026年度）に在籍している
      ✅ 工学部 学士課程（2026年度）の要件の対象年次は1
      ✅ 鈴木花子が〈科目〉を修得したときの学年は1、かつ〈科目〉の単位数は〈単位数〉、であるような〈単位数〉の合計は28
   ✅ 鈴木花子が1年次に修得すべき単位数は31
      ✅ 鈴木花子は工学部 学士課程（2026年度）に在籍している
      ✅ 工学部 学士課程（2026年度）の単位数は124
      ✅ 工学部 学士課程（2026年度）の最低修得割合（%）は25
      ✅ 31は ceiling(124 * 25 / 100) で求める
   ❌ 28が31以上
```

（集約の下にある14科目分の証明は省略しています）

3人の学生では、S001（32単位）と S003（36単位）が満たし、S002（28単位）が満たしません。

## 読み下し

Horn 節と証明木は、`src/horn_reading.py`（Pages 版は `docs/assets/horn.js`）が日本語の文に直します。LLM は使わず、次の材料からの決定論的なテンプレート展開です。そのため読み下しが節の意味から外れることはありません。

| 材料 | 置き場所 | 例 |
|---|---|---|
| 述語の読みの型 | rule-type の `readings` | `"first_year_credits/2": "{1}が1年次に修得した単位数は{2}"` |
| 用語の読みの型 | term ノードの `reading` | `ccso:enrolledIn` → `"{1}は{2}に在籍している"` |
| 変数の呼び名 | rule-type・term の `variables` | `Earned: 修得単位数` → 〈修得単位数〉 |
| 定数の呼び名 | ナレッジグラフの `schema:name` | `urdi:student/S002` → 鈴木花子 |
| 比較・集約・全称 | 処理系側の固定の型 | `>=` → 「以上」、`forall` → 「…であるものすべてについて、…」 |

研究会で「Horn 節を翻訳する何か」と答えた部分にあたります。

## 例題で確かめる（Horn 節が読めない人のために）

Horn 節が正しいかを、Horn 節を読まずに確かめる仕組みです。

1. **AI が例題を作る。** 例題は「仮想の学生の状況」と「その学生について規程から期待される結論」の組です。AI は Horn 節を見ずに、条文の本文（`schema:text`）だけを読んで作ります。境界値の例題は `scripts/generate_examples.py` が機械的に作ります。
2. **システムが同じ学生を判定する。** 例題の学生をナレッジグラフに足して、Horn 節で判定します。
3. **食い違いを並べる。** AI の予想とシステムの判定が一致しない未確認の例題を先頭に並べます。食い違いは、AI の読み違いか、ルールの誤りか、**条文そのものの曖昧さ**のどれかです。
4. **業務担当者は結論だけを見る。** 状況の説明（「現在2年生。2025年度（1年次）に31単位を修得（必修Aを含む）。合計31単位。」）と結論を見て、「正しい／正しくない（正しい結論はこれ）／わからない」を付けます。Horn 節も CURIE も出てきません。
5. **確認済みの例題がテストになる。** 「正しい」「正しくない」を付けた例題は、以後 `pytest` がシステムの判定と照合します。ルールを直して判定が変われば、担当者の結論と合わない例題として検出されます。

「わからない」が付いた例題は、規程の文言を決める側（学務・教授会）へ持ち帰る論点です。

画面では、Streamlit 版の「6. 例題で確かめる（業務担当者向け）」と、Pages 版の `docs/examples.html` で操作します。

## 他システムとのデータ共有

同じファイルを標準的なRDFツールがそのまま読めます。`tests/test_jsonld_interop.py` が rdflib で読み込み、SPARQLで問い合わせられることを検証しています。

```sparql
PREFIX schema: <https://schema.org/>
PREFIX ccso:   <https://w3id.org/ccso/ccso#>
PREFIX course: <https://brightwaltz.github.io/university-rules-demo/id/course/>

# 必修Bを修得していない学生
SELECT ?id WHERE {
  ?student a schema:Person ; schema:identifier ?id .
  FILTER NOT EXISTS { ?student ccso:hasCompleted course:REQ-B }
}
```

判定結果と通知もJSON-LDで書き出せます。通知は `schema:Message`、判定は `urd:DecisionResult` として、根拠条文・使用した用語・前提を伴います。画面の「5. オントロジーとナレッジグラフ」から実効グラフをダウンロードできます。

> インスタンスのIRIは `urdi:course/REQ-A` のように階層を持ちます。SPARQLの接頭辞付き名前はローカル部に `/` を書けないため、上の例のようにコレクションごとの接頭辞を切るか、絶対IRIを `<>` で囲んで使ってください。

## 主な機能

- 3人のダミー学生の切り替えと学生情報表示（グラフからの射影）
- 学生ごとの履修登録・卒業要件通知
- 履修期限、残り履修可能単位、年間上限、卒業要件、必修科目、卒業研究の判定
- 表現ゆれに対応するルールベースIntent Detection
- 適用ルールID、根拠条文ノード、使用したオントロジー用語、Horn節、前提、証明木、読み下し、計算式、不足条件の表示
- `DecisionResult` の内部JSONと、JSON-LD表現の表示
- 画面上でのルール一覧・追加・編集・削除（CRUD、グラフに対する検証つき）
- 複数条件をAND評価するパーソナライズ通知ルール（条件はオントロジー用語で指定）
- Horn節処理系による判定（層化否定・集約つき Datalog、証明木つき）
- 規程の本文・Horn 節・読み下しの並記
- 1年次の修得単位要件（履修規程 第14条。集約とルールの組み合わせの例）
- 例題チェック：AI と境界値生成が作った80件の例題に、業務担当者が「正しい／正しくない／わからない」を付け、確認済みの例題を回帰テストにする
- APIキー・外部サービス・データベース不要

デモでは再現可能性のため、評価日を `2026-04-13` に固定しています。この値は `data/rules.yaml` の `settings.evaluation_date` で変更できます。

## 必要環境

- Python 3.11以上

## インストールと起動

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

`python3.12` は、環境にインストールされている任意のPython 3.11以上のコマンドへ読み替えられます。`python3 --version` でバージョンを確認してください。

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

起動後、通常はブラウザで `http://localhost:8501` が開きます。

## 公開デモ

GitHub Pages向けの静的サイトを `docs/` に収録しています。Pythonサーバーを実行できないGitHub Pages上でも、ブラウザ内の決定論的JavaScriptで動きます。

| ページ | 内容 |
|---|---|
| `docs/index.html` | 学生選択、通知、質問判定、判定根拠、ルールCRUD、語彙対応表 |
| `docs/graph.html` | オントロジーグラフの可視化・編集、グラフ文書の書き出し |
| `docs/examples.html` | 例題チェック。担当者の判断は `localStorage` に保存し、JSON で書き出して `scripts/import_reviews.py` で取り込む |

- ページ自体に `<script type="application/ld+json">` としてナレッジグラフが埋め込まれており、保存すればそのままRDFツールで読み込めます。
- Streamlit版のルール変更は `data/rules.yaml` に保存されます。
- GitHub Pages版のルール変更は閲覧中のブラウザの `localStorage` に保存されます。
- Pages版も外部APIやLLMを使用しません。

### Pages版の同期

Pages版はサーバーを持てないため、ブラウザ内で判定します。判定の論理は JavaScript の手書きではなく、Python 版と同じ Horn 節を `docs/assets/horn.js`（`src/horn.py` の移植）で評価します。JavaScript 側に二重に持つのは、学生の射影、通知の条件、判定結果の文言・値の組み立てなど、Horn 節の外側の部分です。それでも放置すれば乖離するので、次の仕組みで抑えています。

- **語彙とデータは単一情報源**：ナレッジグラフ・ルール・語彙定義・オントロジーグラフはHTMLに埋め込みますが、`scripts/sync_docs_data.py` が `data/` と `src/ontology.py` から生成します。

  ```bash
  python scripts/sync_docs_data.py
  ```

- **判定結果は機械的に照合**：`tests/test_docs_mirror.py` が `docs/index.html` の `<script id="engine">` をNode.jsで実行し、学生の射影・全質問への回答（証明木・前提を含む）・通知・節の読み下しが Python 実装と一致するかを検証します。

- **処理系そのものも照合**：`tests/test_horn_js_mirror.py` が `docs/assets/horn.js` をNode.jsで実行し、構文エラーの文言・形式表記・安全性と層化の検査・解・証明木が `src/horn.py` と一致するかを検証します。

- **例題ページの評価結果は埋め込み**：`docs/examples.html` に載せる各例題のシステムの判定（証明木を含む）は、`scripts/sync_docs_data.py` が Python で計算して埋め込みます。

- **グラフ画面も同様**：`docs/graph.html` の `<script id="graph-engine">` をNode.jsで実行し、導出エッジ・語彙対応表・階層の走査・検証結果が `src/graph_document.py` と一致すること、書き出した YAML が読み戻せることを確認します。加えて最小限のDOMスタブの上で描画と主要操作を通し、例外が出ないことも確かめます。

いずれも Node.js が無い環境ではスキップされます。`data/` や `src/ontology.py` を変更したら、同期スクリプトを実行してください。実行し忘れるとテストが落ちます。

## テスト

仮想環境を有効にした状態で実行します。

```bash
python -m pytest
```

単位上限、卒業要件、卒業研究、通知、未知質問、代表的な表現ゆれ、ルールCRUD、Streamlit画面操作に加えて、オントロジー用語の実在、グラフ文書の構造と壊れた編集の拒否、ナレッジグラフの射影、rdflib/SPARQLでの相互運用、Pages版3ページとの一致をテストしています。さらに、Horn 節処理系（構文・意味・層化・証明木）、読み下し、例題（確認済みの例題とシステムの判定の照合、生成スクリプトの出力とファイルの同期）もテストしています。

## ファイル構成

```text
university-rules-demo/
├── app.py                         # Streamlit UI
├── requirements.txt
├── README.md
├── docs/
│   ├── index.html                 # 公開デモ（判定・通知・ルールCRUD）
│   ├── graph.html                 # オントロジーグラフの可視化・編集
│   ├── examples.html              # 例題チェック（業務担当者向け）
│   └── assets/
│       ├── site.css               # 3ページ共通のスタイル
│       └── horn.js                # Horn節処理系と読み下しのJS版（src/horn.py の移植）
├── data/
│   ├── ontology_graph.yaml        # オントロジーのグラフ文書（node/edge/hypernode、Horn節）
│   ├── university_graph.jsonld    # ナレッジグラフ（学生・科目・規程・組織・修得記録）
│   ├── rules.yaml                 # 規則値と、対象ノード・根拠条文への接続
│   ├── examples.yaml              # 例題と担当者の確認結果
│   ├── schemaorg_terms.json       # schema.org 30.0 の用語名（実在検証用）
│   └── ccso_terms.json            # CCSO 0.7 の用語名（実在検証用）
├── scripts/
│   ├── sync_docs_data.py          # docs/ の埋め込みデータを再生成
│   ├── generate_examples.py       # 境界値の例題を生成して examples.yaml へ合流
│   └── import_reviews.py          # examples.html で書き出した確認結果を取り込む
├── src/
│   ├── graph_document.py          # グラフ文書の読み込み・検証・導出
│   ├── ontology.py                # グラフ文書からの語彙定義とHorn節プログラムの射影
│   ├── knowledge_graph.py         # JSON-LDの読み込みと内部モデルへの射影
│   ├── models.py                  # Pydanticモデル
│   ├── horn.py                    # Horn節インタープリタ（層化否定と集約つきの Datalog）
│   ├── horn_reading.py            # Horn節と証明木の読み下し
│   ├── rule_engine.py             # Horn節の評価と DecisionResult の組み立て
│   ├── rule_repository.py         # rules.yamlの検証・CRUD・原子的保存
│   ├── examples.py                # 例題の読み込み・検証・判定との照合・確認結果の保存
│   ├── jsonld_export.py           # 判定・通知・ルールのJSON-LD出力
│   ├── intent.py                  # Intentのインターフェースと初期実装
│   ├── notification.py            # 個別通知生成
│   └── answer_service.py          # Intentから判定処理へのルーティング
└── tests/
    ├── conftest.py
    ├── js/
    │   ├── check_mirror.mjs           # デモ画面の判定をNode.jsで実行する照合用
    │   ├── check_graph_mirror.mjs     # グラフ画面のモデル層の照合用
    │   ├── check_graph_render.mjs     # グラフ画面の描画スモークテスト
    │   ├── check_horn.mjs             # horn.js を Node.js で実行する照合用
    │   └── check_examples_render.mjs  # 例題ページの描画・書き出しのスモークテスト
    ├── test_app.py
    ├── test_docs_mirror.py
    ├── test_examples.py
    ├── test_examples_page.py
    ├── test_graph_document.py
    ├── test_horn.py
    ├── test_horn_js_mirror.py
    ├── test_horn_reading.py
    ├── test_intent_and_answers.py
    ├── test_jsonld_interop.py
    ├── test_knowledge_graph.py
    ├── test_notifications.py
    ├── test_ontology.py
    ├── test_rule_engine.py
    └── test_rule_repository.py
```

サイトは3ページ構成です。`docs/index.html` が判定・通知・ルールCRUDのデモ、`docs/graph.html` がオントロジーの構造、`docs/examples.html` が例題チェック。共通のヘッダーから相互に行き来でき、スタイルは `docs/assets/site.css` を共有します。

## デモで試す質問

- 履修登録はいつまで？
- 履修登録期限は？
- あと何単位履修できますか？
- あと何単位とれる？
- 年間何単位まで履修できますか？
- 私は卒業要件を満たしていますか？
- 卒業できますか？
- 卒業条件大丈夫？
- 卒業に必要な単位数は？
- 必修科目は足りていますか？
- 卒業研究を履修できますか？
- 1年次の単位要件は満たしていますか？
- 1年次で何単位必要？
- 修学指導の対象ですか？
- 学食のおすすめは？（判定不能の確認）

特に、S001で残り12単位と卒業研究の不足条件、S002で卒業要件不足、S003で卒業要件充足を確認できます。1年次の修得単位要件は S001・S003 が満たし、S002 が満たしません。履修登録通知はS001とS003にだけ表示されます。

## 新しいルールを追加する

画面の「4. ルール管理」から、ルールの確認・追加・編集・削除ができます。変更は `data/rules.yaml` に原子的に保存され、次の画面再実行から回答と通知へ反映されます。

- 既存の制度ルールは、それぞれ1件だけ登録できます。値を変える場合は編集します。
- 制度ルールには、対象ノードと根拠条文の指定が必須です。どちらもナレッジグラフに存在するものだけを選べます。
- 削除した制度ルールは追加画面から再登録できます。
- 必要な制度ルールを削除した場合、関連する質問には推測せず「判定できません」と返します。
- 「個別通知ルール」は複数追加でき、学生に対する複数条件をすべて満たす学生だけに通知します。条件はオントロジー用語（`urd:yearOfStudy`、`ccso:hasCompleted` など）で指定します。
- ルールIDの重複、負数、不正な日付・条件・演算子、グラフに存在しないノードや科目は保存前に拒否されます。

新しい**ルール種別そのもの**を開発する場合は、次の手順です。

1. `data/ontology_graph.yaml` に `rule-type` ノードを追加し、`horn_clause`（実行される節）・`goal`（入口の述語）・`readings`・`variables` を書きます。複雑な規程は、頭部の変数だけを共有する小さな条件の節に分けます。1種別1件にするなら `rule-group/institutional` のメンバーに入れます。
2. schema.org / CCSO に用語が無い場合だけ `term` ノードを `urd:` で追加し、`layer/local` のメンバーに入れ、`reading` と近い用語の `close_match` を書きます。派生用語なら `horn_clause` に定義節を書きます。
3. `src/rule_repository.py` に入力検証を追加します。
4. `src/rule_engine.py` に、入口の述語を Solver に問い合わせて DecisionResult を組み立てるメソッドを追加します（判定の論理は Horn 節側にあり、ここでは文言と値の取り出しだけを書きます）。
5. `app.py` に種別固有の管理フォームを追加し、質問から使う場合は `src/intent.py` と `src/answer_service.py` へ接続します。
6. `docs/index.html` に同じ文言の組み立てを追加し、`scripts/sync_docs_data.py` を実行します（節そのものは埋め込みデータとして同期されるので、JS に論理を書き直す必要はありません）。
7. 例題を `data/examples.yaml` に足し（境界値は `scripts/generate_examples.py` に生成規則を追加）、業務担当者に確認してもらいます。

規則の論理は Horn 節として書き、`src/horn.py` の処理系で評価します。処理系が受け付けるのは安全性と層化を検査に通った節だけで、任意のコードは実行しません。

## 新しい学生や科目を追加する

学生・科目・規程は `data/university_graph.jsonld` に追加します。

1. 科目は `schema:Course` ノードとして `schema:courseCode` と `schema:numberOfCredits` を付けて追加します。
2. 学生は `schema:Person` ＋ `ccso:UndergraduateStudent` として追加し、`ccso:hasCompleted` / `ccso:hasRegistered` で科目へつなぎます。単位数は自動的に合計されます。
3. 履修登録の完了状態は `schema:RegisterAction` の `schema:actionStatus` で表します。
4. 修得の時期が要る学生には、学生×年度ごとに `schema:AchieveAction`（`schema:object` に科目、`urd:atYearOfStudy` に修得時の学年）を置きます。記録を置く場合は、記録の科目の和集合を `ccso:hasCompleted` と一致させてください（読み込み時に検証されます）。
5. 新しい属性が必要なら `src/ontology.py` に用語を定義し、`src/knowledge_graph.py` の射影と `src/models.py` の `Student` を更新します。
6. `python scripts/sync_docs_data.py` を実行してPages版へ反映します。

`Student` は未知フィールドを拒否するため、データ定義の不一致を早期に検出できます。

## 将来の外部連携

置き換え境界は次のように分離されています。

| 現在 | 将来の置き換え候補 | 主な変更箇所 |
|---|---|---|
| `ontology_graph.yaml` | OWL / SKOS / 共通オントロジー基盤 | `GraphDocument` の読み込み元を差し替え。node/edge/hypernode の構造は維持 |
| `university_graph.jsonld` | Personary / PLR / 本格的なKnowledge Graph | `KnowledgeGraph` の読み込み元を差し替え。語彙はそのまま |
| 自前のJSON-LDローダー | rdflib / トリプルストア / SPARQLエンドポイント | `KnowledgeGraph` の内部実装のみ |
| `rules.yaml` | 本格的Rules as Code基盤 | `RuleEngine` の規則取得部分をアダプター化 |
| 自前の Horn 節処理系（Datalog＋層化否定＋集約） | グラフを項として扱う推論エンジン | 事実の作り方（`FactBase.from_jsonld`）と `Solver` を差し替え。節と読み下しの型はそのまま |
| Streamlit | 大学ポータル / スマートフォンアプリ | `AnswerService` と通知サービスをAPIとして公開 |
| `RuleBasedIntentDetector` | OpenAI / Gemini | `IntentDetector` インターフェースの実装を差し替え |
| 画面内通知 | Push / Email / LINE | `Notification` の配信アダプターを追加 |

### 共通オントロジーで接続する場合

外部データを取り込むときも、`schema:` / `ccso:` の用語で表現されている限り `KnowledgeGraph` の射影を変えるだけで済みます。外部側が別の語彙を使う場合は、`src/ontology.py` の対応表に写像を追加し、読み込み時に正規化してください。外部ID、データ取得日時、同意・アクセス制御、欠損値の扱いも明示してください。Rule Engine へ外部サービス固有の形式を直接流し込まないことが重要です。

### LLM連携時の注意事項

LLMを導入する場合も、許可する用途はIntent Detectionと文章表現の補助に限定します。

- LLM出力を制度上の最終判定として使わない
- `DecisionResult` をLLMに生成・変更させない
- LLMが返したIntentは許可済みEnumへ検証してから使う
- 回答の数値・可否・規則ID・出典は `DecisionResult` からのみ取得する
- unknown時に一般知識で補完しない
- プロンプト、モデルバージョン、入力、出力を監査可能にする

これにより、自然言語処理を高度化しても決定論的な制度判断の境界を維持できます。ナレッジグラフと用語定義が外に出ているため、LLMに渡す文脈も「どの用語のどのノードを見たか」の単位で監査できます。

例題の作成は、LLM に任せてよい作業です。例題は判定には使われず、業務担当者の確認を経て初めてテストになるためです。本デモの AI 例題（origin: ai）は、Horn 節を見せずに規程の本文だけから作っています。

## 参考

- schema.org 30.0（2026-03-19）https://schema.org/
- CCSO — Curriculum Course Syllabus Ontology https://github.com/eVgKatis/CCSO
- OLOUD — An Ontology for Linked Open University Data, Acta Polytechnica Hungarica 14(4), 2017
- plrDart（KG-WAM）https://github.com/assemblogue/plrDart
