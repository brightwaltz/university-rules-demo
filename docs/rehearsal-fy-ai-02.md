# リハーサル手順：例題 FY-AI-02（編入学）から第14条に節を足す

会議で次の一巡を再現するための手順です。

1. 担当者が例題 FY-AI-02（3年次に編入学、修得記録なし）に「判定すべきでない」と答える（AI の予想 `unknown` に `agree`）
2. その確認結果を `scripts/import_reviews.py` で取り込む
3. pytest が赤になる（担当者の答えは `unknown`、システムは 0 単位なので `not_eligible`）
4. 履修規程第14条に「編入学生は対象外」の節を足す
5. pytest が緑に戻る

ブランチ `demo/transfer-exception` の2つのコミットを使います。

| 呼び名 | コミット | 中身 |
|---|---|---|
| コミット 1 | `demo/transfer-exception~1` | 学生に入学区分（`urd:admissionCategory`）を持たせ、FY-AI-02 を編入学の学生にした。ルールは変えていない |
| コミット 2 | `demo/transfer-exception` | 第14条に適用条件の節 `first_year_requirement_applies` を足した（この手順書もここに入っている） |

この2つの下に、例題ページのテストを直したコミットが1つあります（手順には関係しません）。

コマンドはリポジトリの最上位で実行します。Python は `.venv/bin/python` です。

## 0. 始める前に

作業ツリーがクリーンであることを確かめます（手順の最後で `git reset --hard` するため、残したい変更があれば先にコミットか退避をしておく）。

```sh
git status --short    # 何も出なければよい
```

## 1. コミット 1 を取り出す

```sh
git switch --detach demo/transfer-exception~1
.venv/bin/python -m pytest -q    # 全件通る（FY-AI-02 は未確認なので、食い違っていても失敗にはしない）
```

## 2. 担当者の確認結果を取り込む

画面（`docs/examples.html`）の「確認結果を書き出す」で作るものと同じ形の JSON です。リポジトリの外に置きます。

```sh
cat > /tmp/fy-ai-02-review.json <<'JSON'
{"examples":[{"id":"FY-AI-02","verdict":"agree","reviewer":"教務担当","reviewed_on":"2026-11-12"}]}
JSON
.venv/bin/python scripts/import_reviews.py /tmp/fy-ai-02-review.json
.venv/bin/python scripts/sync_docs_data.py
```

`agree` は「AI の結論（`unknown`＝このルールで判定すべきでない）は正しい」という意味です。取り込みで `data/examples.yaml` の FY-AI-02 の `review` が書き換わり、同期で `docs/examples.html` の埋め込みが更新されます。

## 3. pytest が赤になる

```sh
.venv/bin/python -m pytest -q
```

`tests/test_examples.py::test_reviewed_examples_match_the_system` が失敗します。

```text
担当者が確認した例題とシステムの判定が食い違っています:
FY-AI-02: 期待 unknown / システム not_eligible（担当者のメモ: None）
```

担当者が確認した例題は回帰テストになるので、確認した結論とシステムの判定が違えば失敗します。

## 4. 第14条に「編入学生は対象外」の節を足す

ルール側のファイルだけをコミット 2 から持ってきます。取り込みで変わった `data/examples.yaml` と `docs/examples.html` には触れないので、衝突しません。

```sh
git checkout demo/transfer-exception -- \
  data/ontology_graph.yaml \
  src/rule_engine.py \
  docs/index.html \
  tests/test_examples.py \
  tests/test_rule_engine.py \
  tests/test_docs_mirror.py
.venv/bin/python scripts/sync_docs_data.py
```

足した節（`data/ontology_graph.yaml` の `rule-type/first_year_credit_requirement`）：

```prolog
% 第14条の適用条件：編入学生は対象外（本学での第1年次がないため）
first_year_requirement_applies(Student) :-
    ccso:enrolledIn(Student, Program),
    \+ urd:admissionCategory(Student, urdi:term/transfer-admission).
```

- `src/rule_engine.py` と `docs/index.html`（Pages 版の判定）は、要件を問う前にこの節を問い合わせ、成り立たなければ「編入学のため、履修規程第14条（第1年次の修得単位）の対象外です。」（`unknown`）を返します。
- テストの3ファイルは、FY-AI-02 が既知の食い違いから外れたことと、編入学生の判定（Python と JS の照合を含む）を確かめるものです。
- 同期で、Pages 版の語彙・Horn 節の埋め込みと `docs/graph.html` が更新されます。

## 5. pytest が緑に戻る

```sh
.venv/bin/python -m pytest -q
```

全件通ります。FY-AI-02 は、担当者の答え（`unknown`）とシステムの判定が一致するようになりました。

## 元に戻す

取り込んだ確認結果はコミットしません。作業ツリーを捨ててブランチへ戻ります。

```sh
git reset --hard
git switch demo/transfer-exception
git status --short    # 何も出なければよい
rm /tmp/fy-ai-02-review.json
```
