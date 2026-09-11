# University Rules as Code Assistant

学生のパーソナルデータと大学規則を組み合わせ、履修・卒業に関する回答、個別通知、適格性判定を行う研究用MVPです。APIキーは不要で、大学制度上の判断に生成AIを使用しません。

データは **schema.org を基底とする共通オントロジー上のナレッジグラフ**（`data/university_graph.jsonld`）として保持し、規則は **そのオントロジーの用語に束縛されたRules as Code**（`data/rules.yaml`）として保持します。

そのオントロジー自体も **node / edge / hypernode からなるグラフ文書**（`data/ontology_graph.yaml`）として持ち、[オントロジーグラフ画面](docs/graph.html)で可視化・編集できます。

## このデモにおける Rules as Code

```text
自然言語の質問
    ↓
RuleBased Intent Detection
    ↓
ナレッジグラフ（schema.org ＋ CCSO ＋ ローカル拡張）
    ＋ Rules YAML（各ルールは条文ノードと対象ノードに接続）
    ↓
決定論的なRule Engine
    ↓
DecisionResult（回答・根拠条文・使用した用語・Horn節・前提・不足条件）
    ↓
回答画面 / パーソナライズ通知 / JSON-LD出力
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
| 修得単位数の合計 | `urd:earnedCredits` | — | — | — |
| 年間履修上限 | `urd:annualCreditLimit` | — | — | — |
| 卒業必修科目 | `urd:requiredCourse` | `schema:programPrerequisites`（closeMatch） | `ccso:hasPrerequisite`（closeMatch） | `oloud:subjectRequires`（closeMatch） |

この表は手で保守していません。`data/ontology_graph.yaml` の concept → term エッジから毎回導出しています（後述の[オントロジーグラフ](#オントロジーグラフグラフ文書)）。画面の「5. オントロジーとナレッジグラフ」でも参照できます。

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
| `edges` | 有向の関係。端点には hypernode も取れる | `source, target, kind, label` |
| `hypernodes` | メンバーを持つノード。入れ子にできる | `id, kind, label, members, about, statement, evidence` |

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
| エッジ | **始点と終点の付け替え**、関係の種類、ラベルの編集、削除 |
| ハイパーノード | ラベル・言明・根拠の編集、**メンバー／対象の選び直し**、削除 |
| 追加 | ノード／エッジ／ハイパーノードをフォームから追加 |

導出エッジ（鍵アイコン）は編集できません。元になっている `rule_terms` や `close_match` を直すと変わります。削除しようとすると、巻き込まれる関係の本数を先に知らせます。

編集は閲覧中のブラウザの `localStorage` に保存され、**同じ形式の YAML として書き出せます**。書き出したファイルを `data/ontology_graph.yaml` へ置き、`python scripts/sync_docs_data.py && python -m pytest` を実行すればリポジトリへ取り込めます。

書き出した YAML が実際に読み戻せることは `tests/test_docs_mirror.py` が検証しています（Node.js で画面のモデル層を実行し、Python の読み込み結果と突き合わせ）。

## ナレッジグラフ

`data/university_graph.jsonld` に、大学・学部・課程・学位・科目カタログ・規程（条文単位）・学生・履修登録行為を1つのJSON-LD文書として持ちます。

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

`KnowledgeGraph.students()` がこのグラフを内部モデル `Student` へ射影し、Rule Engine は従来どおり `Student` を見て判定します。外部データ源を差し替えるときは、この射影だけを書き換えます。

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

規則値をグラフ本体には書きません。`rules.yaml` が唯一の情報源で、外部へ書き出すときだけ対象ノードへ重ねた「実効グラフ」として1文書にまとめます（`src/jsonld_export.py` の `effective_graph`）。

## Horn節と前提

各ルール種別は、判定をHorn節として書き下したものを持ちます。処理系には渡さず、**どの原子論理式を根拠にしたかを人間が確認するため**に使います。

```prolog
eligible_to_register(Student, Thesis) :-
  urd:minimumYearOfStudy(Thesis, MinYear), urd:yearOfStudy(Student, Year), Year >= MinYear,
  urd:minimumEarnedCredits(Thesis, MinCredits), urd:earnedCredits(Student, Earned), Earned >= MinCredits,
  forall(schema:coursePrerequisites(Thesis, Course), ccso:hasCompleted(Student, Course)).
```

判定結果（`DecisionResult`）には、この本体を実際の値で具体化した `premises` が入ります。各前提は「使った用語（CURIE）／対象ノード／実際の値／比較演算子／要求値／充足したか」を持つため、どの原子論理式が失敗して不許可になったのかを1件ずつ追えます。

現時点では**1段の導出**までで、証明木の構築や複数ユーザによる並行証明は行いません。Horn節処理系へ接続する場合の接続点を明示することを目的としています。

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
- 適用ルールID、根拠条文ノード、使用したオントロジー用語、Horn節、前提、計算式、不足条件の表示
- `DecisionResult` の内部JSONと、JSON-LD表現の表示
- 画面上でのルール一覧・追加・編集・削除（CRUD、グラフに対する検証つき）
- 複数条件をAND評価するパーソナライズ通知ルール（条件はオントロジー用語で指定）
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

- ページ自体に `<script type="application/ld+json">` としてナレッジグラフが埋め込まれており、保存すればそのままRDFツールで読み込めます。
- Streamlit版のルール変更は `data/rules.yaml` に保存されます。
- GitHub Pages版のルール変更は閲覧中のブラウザの `localStorage` に保存されます。
- Pages版も外部APIやLLMを使用しません。

### Pages版の同期

Pages版はサーバーを持てないため、判定ロジックをJavaScriptで二重に実装しています。放置すれば必ず乖離するので、次の仕組みで抑えています。

- **語彙とデータは単一情報源**：ナレッジグラフ・ルール・語彙定義・オントロジーグラフはHTMLに埋め込みますが、`scripts/sync_docs_data.py` が `data/` と `src/ontology.py` から生成します。

  ```bash
  python scripts/sync_docs_data.py
  ```

- **判定結果は機械的に照合**：`tests/test_docs_mirror.py` が `docs/index.html` の `<script id="engine">` をNode.jsで実行し、学生の射影・全質問への回答・通知が Python 実装と一致するかを検証します。

- **グラフ画面も同様**：`docs/graph.html` の `<script id="graph-engine">` をNode.jsで実行し、導出エッジ・語彙対応表・階層の走査・検証結果が `src/graph_document.py` と一致すること、書き出した YAML が読み戻せることを確認します。加えて最小限のDOMスタブの上で描画と主要操作を通し、例外が出ないことも確かめます。

いずれも Node.js が無い環境ではスキップされます。`data/` や `src/ontology.py` を変更したら、同期スクリプトを実行してください。実行し忘れるとテストが落ちます。

## テスト

仮想環境を有効にした状態で実行します。

```bash
python -m pytest
```

単位上限、卒業要件、卒業研究、通知、未知質問、代表的な表現ゆれ、ルールCRUD、Streamlit画面操作に加えて、オントロジー用語の実在、グラフ文書の構造と壊れた編集の拒否、ナレッジグラフの射影、rdflib/SPARQLでの相互運用、Pages版2ページとの一致をテストしています。

## ファイル構成

```text
university-rules-demo/
├── app.py                         # Streamlit UI
├── requirements.txt
├── README.md
├── docs/
│   ├── index.html                 # 公開デモ（判定・通知・ルールCRUD）
│   ├── graph.html                 # オントロジーグラフの可視化・編集
│   └── assets/site.css            # 2ページ共通のスタイル
├── data/
│   ├── ontology_graph.yaml        # オントロジーのグラフ文書（node/edge/hypernode）
│   ├── university_graph.jsonld    # ナレッジグラフ（学生・科目・規程・組織）
│   ├── rules.yaml                 # 規則値と、対象ノード・根拠条文への接続
│   ├── schemaorg_terms.json       # schema.org 30.0 の用語名（実在検証用）
│   └── ccso_terms.json            # CCSO 0.7 の用語名（実在検証用）
├── scripts/
│   └── sync_docs_data.py          # docs/ の埋め込みデータを再生成
├── src/
│   ├── graph_document.py          # グラフ文書の読み込み・検証・導出
│   ├── ontology.py                # グラフ文書からの語彙定義の射影
│   ├── knowledge_graph.py         # JSON-LDの読み込みと内部モデルへの射影
│   ├── models.py                  # Pydanticモデル
│   ├── rule_engine.py             # 決定論的な条件評価
│   ├── rule_repository.py         # rules.yamlの検証・CRUD・原子的保存
│   ├── jsonld_export.py           # 判定・通知・ルールのJSON-LD出力
│   ├── intent.py                  # Intentのインターフェースと初期実装
│   ├── notification.py            # 個別通知生成
│   └── answer_service.py          # Intentから判定処理へのルーティング
└── tests/
    ├── conftest.py
    ├── js/
    │   ├── check_mirror.mjs        # デモ画面の判定をNode.jsで実行する照合用
    │   ├── check_graph_mirror.mjs  # グラフ画面のモデル層の照合用
    │   └── check_graph_render.mjs  # グラフ画面の描画スモークテスト
    ├── test_app.py
    ├── test_docs_mirror.py
    ├── test_graph_document.py
    ├── test_intent_and_answers.py
    ├── test_jsonld_interop.py
    ├── test_knowledge_graph.py
    ├── test_notifications.py
    ├── test_ontology.py
    ├── test_rule_engine.py
    └── test_rule_repository.py
```

サイトは2ページ構成です。`docs/index.html` が判定・通知・ルールCRUDのデモ、`docs/graph.html` がオントロジーの構造。共通のヘッダーから相互に行き来でき、スタイルは `docs/assets/site.css` を共有します。

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
- 学食のおすすめは？（判定不能の確認）

特に、S001で残り12単位と卒業研究の不足条件、S002で卒業要件不足、S003で卒業要件充足を確認できます。履修登録通知はS001とS003にだけ表示されます。

## 新しいルールを追加する

画面の「4. ルール管理」から、ルールの確認・追加・編集・削除ができます。変更は `data/rules.yaml` に原子的に保存され、次の画面再実行から回答と通知へ反映されます。

- 既存の制度ルールは、それぞれ1件だけ登録できます。値を変える場合は編集します。
- 制度ルールには、対象ノードと根拠条文の指定が必須です。どちらもナレッジグラフに存在するものだけを選べます。
- 削除した制度ルールは追加画面から再登録できます。
- 必要な制度ルールを削除した場合、関連する質問には推測せず「判定できません」と返します。
- 「個別通知ルール」は複数追加でき、学生に対する複数条件をすべて満たす学生だけに通知します。条件はオントロジー用語（`urd:yearOfStudy`、`ccso:hasCompleted` など）で指定します。
- ルールIDの重複、負数、不正な日付・条件・演算子、グラフに存在しないノードや科目は保存前に拒否されます。

新しい**ルール種別そのもの**を開発する場合は、次の手順です。

1. `data/ontology_graph.yaml` に `rule-type` ノードを追加し、対象クラス・用語・Horn節を書きます（画面から追加して書き出しても構いません）。1種別1件にするなら `rule-group/institutional` のメンバーに入れます。
2. schema.org / CCSO に用語が無い場合だけ `term` ノードを `urd:` で追加し、`layer/local` のメンバーに入れ、近い用語を `close_match` に書きます。概念を増やすときは `concept` ノードと `uses` / `alternative` エッジを足せば、語彙対応表には自動で載ります。
3. `src/rule_repository.py` に入力検証を追加します。
4. `src/rule_engine.py` に、そのルールを読み取る明示的な評価メソッドを追加します。
5. `app.py` に種別固有の管理フォームを追加します。
6. 質問から利用する場合は `src/intent.py` と `src/answer_service.py` へ接続します。
7. 境界値・合格・不合格・ルール欠落のテストを追加し、`docs/index.html` へ移植して `scripts/sync_docs_data.py` を実行します。

YAMLの条件式をそのまま実行する設計にはしていません。任意コード実行や解釈の曖昧さを避け、レビュー可能なPython条件式を意図的に採用しています。

## 新しい学生や科目を追加する

学生・科目・規程は `data/university_graph.jsonld` に追加します。

1. 科目は `schema:Course` ノードとして `schema:courseCode` と `schema:numberOfCredits` を付けて追加します。
2. 学生は `schema:Person` ＋ `ccso:UndergraduateStudent` として追加し、`ccso:hasCompleted` / `ccso:hasRegistered` で科目へつなぎます。単位数は自動的に合計されます。
3. 履修登録の完了状態は `schema:RegisterAction` の `schema:actionStatus` で表します。
4. 新しい属性が必要なら `src/ontology.py` に用語を定義し、`src/knowledge_graph.py` の射影と `src/models.py` の `Student` を更新します。
5. `python scripts/sync_docs_data.py` を実行してPages版へ反映します。

`Student` は未知フィールドを拒否するため、データ定義の不一致を早期に検出できます。

## 将来の外部連携

置き換え境界は次のように分離されています。

| 現在 | 将来の置き換え候補 | 主な変更箇所 |
|---|---|---|
| `ontology_graph.yaml` | OWL / SKOS / 共通オントロジー基盤 | `GraphDocument` の読み込み元を差し替え。node/edge/hypernode の構造は維持 |
| `university_graph.jsonld` | Personary / PLR / 本格的なKnowledge Graph | `KnowledgeGraph` の読み込み元を差し替え。語彙はそのまま |
| 自前のJSON-LDローダー | rdflib / トリプルストア / SPARQLエンドポイント | `KnowledgeGraph` の内部実装のみ |
| `rules.yaml` | 本格的Rules as Code基盤 | `RuleEngine` の規則取得部分をアダプター化 |
| 1段の導出（premises） | Horn節処理系・証明木 | `DecisionResult.premises` を証明木ノードへ拡張 |
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

## 参考

- schema.org 30.0（2026-03-19）https://schema.org/
- CCSO — Curriculum Course Syllabus Ontology https://github.com/eVgKatis/CCSO
- OLOUD — An Ontology for Linked Open University Data, Acta Polytechnica Hungarica 14(4), 2017
