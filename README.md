# University Rules as Code Assistant

学生のパーソナルデータと大学規則を組み合わせ、履修・卒業に関する回答、個別通知、適格性判定を行う研究用MVPです。APIキーは不要で、大学制度上の判断に生成AIを使用しません。

## このデモにおける Rules as Code

大学規則の根拠・基準値を `data/rules.yaml` に機械可読な形で保持し、`src/rule_engine.py` の明示的な条件式で評価します。自然言語の質問はルールベースでIntentへ分類するだけであり、最終的な可否判断は必ずRule Engineが行います。

```text
自然言語の質問
    ↓
RuleBased Intent Detection
    ↓
Rules YAML ＋ Student JSON
    ↓
決定論的なRule Engine
    ↓
DecisionResult（回答・根拠・事実・不足条件）
    ↓
回答画面 / パーソナライズ通知
```

登録ルールで扱えない質問には、推測せず「このプロトタイプに登録されているルールでは判定できません」と返します。

## 主な機能

- 3人のダミー学生の切り替えと学生情報表示
- 学生ごとの履修登録・卒業要件通知
- 履修期限、残り履修可能単位、年間上限、卒業要件、必修科目、卒業研究の判定
- 表現ゆれに対応するルールベースIntent Detection
- 適用ルールID、規則名、出典、使用事実、計算式、不足条件の表示
- `DecisionResult` の内部JSON表示
- 画面上でのルール一覧・追加・編集・削除（CRUD）
- 複数条件をAND評価するパーソナライズ通知ルール
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

GitHub Pages向けの静的デモを `docs/index.html` に収録しています。Pythonサーバーを実行できないGitHub Pages上でも、学生選択、通知、質問判定、判定根拠、ルールCRUDをブラウザ内の決定論的JavaScriptで実行します。

- Streamlit版のルール変更は `data/rules.yaml` に保存されます。
- GitHub Pages版のルール変更は閲覧中のブラウザの `localStorage` に保存されます。
- Pages版も外部APIやLLMを使用しません。

## テスト

仮想環境を有効にした状態で実行します。

```bash
python -m pytest
```

単位上限、卒業要件、卒業研究、通知、未知質問、代表的な表現ゆれ、ルールCRUD、およびStreamlit画面操作をテストしています。

## ファイル構成

```text
university-rules-demo/
├── app.py                         # Streamlit UI
├── requirements.txt
├── README.md
├── data/
│   ├── students.json              # 学生パーソナルデータ
│   └── rules.yaml                 # 規則値と根拠
├── src/
│   ├── models.py                  # Pydanticモデル
│   ├── rule_engine.py             # 決定論的な条件評価
│   ├── rule_repository.py         # rules.yamlの検証・CRUD・原子的保存
│   ├── intent.py                  # Intentのインターフェースと初期実装
│   ├── notification.py            # 個別通知生成
│   └── answer_service.py          # Intentから判定処理へのルーティング
└── tests/
    ├── conftest.py
    ├── test_app.py
    ├── test_rule_repository.py
    ├── test_rule_engine.py
    ├── test_notifications.py
    └── test_intent_and_answers.py
```

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
- 削除した制度ルールは追加画面から再登録できます。
- 必要な制度ルールを削除した場合、関連する質問には推測せず「判定できません」と返します。
- 「個別通知ルール」は複数追加でき、学生属性に対する複数条件をすべて満たす学生だけに通知します。
- ルールIDの重複、負数、不正な日付・条件・演算子は保存前に拒否されます。

新しい**ルール種別そのもの**を開発する場合は、次の手順です。

1. `src/rule_repository.py` に種別名と入力検証を追加します。
2. `src/rule_engine.py` に、そのルールを読み取る明示的な評価メソッドを追加します。
3. `app.py` に種別固有の管理フォームを追加します。
4. 質問から利用する場合は `src/intent.py` と `src/answer_service.py` へ接続します。
5. 境界値・合格・不合格・ルール欠落のテストを追加します。

YAMLの条件式をそのまま実行する設計にはしていません。任意コード実行や解釈の曖昧さを避け、レビュー可能なPython条件式を意図的に採用しています。

## 新しい学生属性を追加する

1. `src/models.py` の `Student` に型付きフィールドを追加します。
2. `data/students.json` の全学生へ値を追加します。
3. その属性を使うRule Engineの評価とテストを追加します。
4. 必要に応じて `app.py` の学生情報カードを更新します。

`Student` は未知フィールドを拒否するため、データ定義の不一致を早期に検出できます。

## 将来の外部連携

置き換え境界は次のように分離されています。

| 現在 | 将来の置き換え候補 | 主な変更箇所 |
|---|---|---|
| `students.json` | Personary / PLR / Knowledge Graph | 学生リポジトリ層を追加し、`Student` へ正規化 |
| `rules.yaml` | 本格的Rules as Code基盤 / KG | `RuleEngine` の規則取得部分をアダプター化 |
| Streamlit | 大学ポータル / スマートフォンアプリ | `AnswerService` と通知サービスをAPIとして公開 |
| `RuleBasedIntentDetector` | OpenAI / Gemini | `IntentDetector` インターフェースの実装を差し替え |
| 画面内通知 | Push / Email / LINE | `Notification` の配信アダプターを追加 |

### Personary / PLR / Knowledge Graphへ接続する場合

`Student` を共通の内部スキーマとして維持し、外部データをこのモデルへ変換するRepository/Adapterを `load_students()` の手前に追加します。外部ID、データ取得日時、同意・アクセス制御、欠損値の扱いも明示してください。Rule Engineへ外部サービス固有の形式を直接流し込まないことが重要です。

### LLM連携時の注意事項

LLMを導入する場合も、許可する用途はIntent Detectionと文章表現の補助に限定します。

- LLM出力を制度上の最終判定として使わない
- `DecisionResult` をLLMに生成・変更させない
- LLMが返したIntentは許可済みEnumへ検証してから使う
- 回答の数値・可否・規則ID・出典は `DecisionResult` からのみ取得する
- unknown時に一般知識で補完しない
- プロンプト、モデルバージョン、入力、出力を監査可能にする

これにより、自然言語処理を高度化しても決定論的な制度判断の境界を維持できます。
