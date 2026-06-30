/goal 次の「リファクタリング前環境準備ワークフロー」を自律的に実行する。

この goal は、本番のリファクタリング作業を開始する前の準備専用である。
この goal では、アプリケーション本体のリファクタリング、仕様変更、機能修正、テスト期待値の変更は行わない。

目的は、後続のリファクタリング goal を安全かつ再現性高く実行できる状態にすることである。

【目的】
以下を確認・整備し、後続のリファクタリング goal を実行してよいか判定する。

- Codex subagent が利用可能か
- project-scoped custom subagent を作成・利用できるか
- 調査、実装、レビュー、テスト、E2E確認を担当する subagent 定義があるか
- E2E テスト環境が存在するか
- E2E テストを実行できるか
- E2E テストが実行できない場合、低リスクに構築または準備できるか
- build / lint / typecheck / unit test / integration test / e2e test の検証コマンドが特定できるか
- 後続のリファクタリング goal に渡すべき制約、コマンド、既存失敗、環境リスクを記録できるか

【最重要原則】
- この goal では本番コードをリファクタリングしない。
- アプリケーションの外部仕様、公開API、CLI、設定仕様、DBスキーマ、画面仕様、テスト期待値を変更しない。
- テストを削除、skip、無効化、期待値緩和、snapshot更新によって通すことは禁止する。
- 依存関係の追加、lockfile更新、Docker構成変更、CI設定変更は原則行わない。
- ただし、E2E環境準備に必要で、既存ドキュメントまたは既存設定から明確に導ける低リスクな補助ファイル作成は許可する。
- 既存ファイルを上書きしない。
- ユーザーの未コミット変更を上書き、整形、削除しない。
- 認証情報、APIキー、実環境データ、外部本番サービスを使わない。
- ネットワークアクセスが必要な操作は、現在のCodex実行環境で許可されている場合のみ行う。
- ネットワーク、Docker、ブラウザ、DB、外部サービス、認証情報が必要で利用できない場合は、無理に突破せず BLOCKED または READY_WITH_LIMITATIONS として記録する。

【成果物】
この goal の成果物は以下とする。

必須:
- docs/refactoring-preflight-report.md

条件付きで作成してよい:
- .codex/agents/refactor_explorer.toml
- .codex/agents/refactor_implementer.toml
- .codex/agents/refactor_reviewer.toml
- .codex/agents/refactor_tester.toml
- .codex/agents/e2e_probe.toml
- docs/e2e-environment.md
- scripts/check-e2e-env.sh または scripts/check-e2e-env.js

作成禁止:
- 本番コードのリファクタリング差分
- package.json / pyproject.toml / Cargo.toml / go.mod などの依存関係変更
- lockfile
- DB migration
- CI設定
- 本番設定ファイル
- snapshot / golden output
- generated files
- .env や秘密情報ファイル

ただし、既存プロジェクトが明示的に E2E 環境構築用のファイルを持ち、低リスクな追記だけで検証可能になる場合は、変更内容、理由、リスク、差分を docs/refactoring-preflight-report.md に記録したうえで最小限の補助ファイルのみ作成してよい。

【判定ステータス】
最終的に以下のいずれかで判定する。

READY:
- 後続リファクタリング goal を安全に開始できる。
- subagent 定義が存在し、spawn または利用可能性を確認できた。
- 主要な検証コマンドが特定できている。
- E2E テストまたは代替のUI/統合検証が実行可能である。
- 既存失敗がある場合は記録済みで、新規失敗との区別が可能である。

READY_WITH_LIMITATIONS:
- 後続リファクタリング goal は開始可能だが、制限がある。
- 例:
  - E2E は存在するが一部外部サービスが必要
  - E2E は存在するが現環境ではブラウザやDockerが不足
  - E2E はなく、unit / integration / smoke test で代替する必要がある
  - subagent は built-in agent のみ利用可能
  - custom subagent 定義は作成したが、project trust の都合で読み込み確認ができない

BLOCKED:
- 後続リファクタリング goal を開始すべきでない。
- 例:
  - git 状態が不明または既存変更と衝突する
  - 検証コマンドが特定できない
  - 依存関係が未導入で、インストールも安全にできない
  - E2E 以前に通常テストやビルドが実行不能
  - subagent の独立レビュー運用が確認できず、代替リスクも高い
  - 必要な認証情報や外部サービスが不明
  - 環境構築に大きな設定変更や依存追加が必要

【工程0：安全確認】
1. git status --short を実行する。
2. 未コミット変更がある場合、以下を記録する。
   - 変更ファイル
   - 既存変更と判断した理由
   - この goal の変更予定領域と衝突するか
3. 以下に既存変更がある場合は、上書きせず BLOCKED または READY_WITH_LIMITATIONS とする。
   - .codex/
   - docs/refactoring-preflight-report.md
   - package manager files
   - test config
   - E2E config
   - CI config
4. 現在のブランチ名、リポジトリルート、作業ディレクトリを記録する。
5. docs/refactoring-preflight-report.md の初期版を作成する。
6. docs/refactoring-preflight-report.md に以下を記録する。
   - 実施日時
   - リポジトリルート
   - git 状態
   - 未コミット変更の有無
   - この goal で変更してよい領域
   - この goal で変更しない領域

【工程1：プロジェクト構造と指示ファイルの確認】
以下を読み、後続 goal に必要な前提を整理する。

- AGENTS.md
- AGENTS.override.md
- README
- CONTRIBUTING
- docs/
- Makefile
- package.json
- pnpm-lock.yaml / yarn.lock / package-lock.json / bun.lockb
- pyproject.toml / requirements.txt / uv.lock / poetry.lock
- Cargo.toml / Cargo.lock
- go.mod / go.sum
- Gemfile / Gemfile.lock
- composer.json / composer.lock
- Dockerfile
- docker-compose.yml / compose.yml
- CI設定
- lint設定
- typecheck設定
- test設定
- e2e設定
- playwright.config.*
- cypress.config.*
- vitest.config.*
- jest.config.*
- pytest.ini
- tox.ini
- rspec設定
- rails system test設定
- detox設定
- maestro設定
- その他プロジェクト固有のテスト設定

docs/refactoring-preflight-report.md に以下を記録する。

- プロジェクト種別
- 推定技術スタック
- パッケージマネージャ
- 起動方法
- テスト方法
- E2E候補
- CI上で実行されている検証コマンド
- 後続リファクタリング goal が必ず読むべきファイル
- 後続リファクタリング goal が変更すべきでない領域

【工程2：Codex subagent 環境の確認】
1. 以下を確認する。
   - .codex/ ディレクトリの有無
   - .codex/config.toml の有無
   - .codex/agents/ の有無
   - 既存の .codex/agents/*.toml
   - 可能であれば ~/.codex/agents/ の存在
   - Codex が project-scoped .codex/ 設定を読み込める状態か
   - subagent spawn が利用可能か
2. 既存 custom agent がある場合は、上書きしない。
3. 既存 custom agent の役割が今回の用途に使える場合は再利用候補として記録する。
4. 不足している場合、次の project-scoped custom agents を .codex/agents/ に作成する。
5. ただし、既存ファイル名と衝突する場合は作成せず、別名候補を docs/refactoring-preflight-report.md に記録する。

作成する agent は以下。

### refactor_explorer
用途:
- 読み取り専用調査
- アーキテクチャ、責務、依存関係、複雑度、テスト不足の洗い出し
- 実装は禁止

作成ファイル:
.codex/agents/refactor_explorer.toml

内容:
```toml
name = "refactor_explorer"
description = "Read-only explorer for refactoring investigations. Maps code paths, responsibilities, risks, and test coverage before changes."
sandbox_mode = "read-only"
developer_instructions = """
あなたはリファクタリング調査専用の読み取り専用エージェントである。
コードを変更してはならない。
対象範囲の責務、依存関係、重複、複雑度、型安全性、エラー処理、テスト不足、E2E影響を調査する。
推測ではなく、確認したファイル、関数、設定、テストを根拠に報告する。
報告では以下を必ず含める。
- 確認したファイル
- 問題視した内容
- 根拠
- 外部仕様への影響可能性
- 推奨対応
- 実装リスク
- 検証すべきテスト
"""
```

### refactor_implementer
用途:
- 限定された改善項目のみを最小差分で実装
- 無関係な整形は禁止

作成ファイル:
.codex/agents/refactor_implementer.toml

内容:
```toml
name = "refactor_implementer"
description = "Implementation agent for one bounded refactoring item at a time. Makes minimal safe changes and runs targeted validation."
sandbox_mode = "workspace-write"
developer_instructions = """
あなたは限定実装専用エージェントである。
オーケストレーターから指定された改善項目だけを最小差分で修正する。
外部仕様、公開API、CLI、設定、DBスキーマ、依存関係、lockfile、CI設定、生成物を変更してはならない。
テストの削除、skip、期待値緩和、snapshot更新で合格扱いにしてはならない。
無関係な整形、リネーム、移動は禁止する。
実装後は、変更内容、変更ファイル、主要diff、実行コマンド、結果、残存リスクを報告する。
"""
```

### refactor_reviewer
用途:
- 独立レビュー
- 読み取り専用
- PASS / FAIL を根拠付きで返す

作成ファイル:
.codex/agents/refactor_reviewer.toml

内容:
```toml
name = "refactor_reviewer"
description = "Read-only reviewer for bounded refactoring diffs. Checks behavior preservation, tests, risk, and unnecessary changes."
sandbox_mode = "read-only"
developer_instructions = """
あなたは独立レビュー専用の読み取り専用エージェントである。
コードを変更してはならない。
修正計画、受入条件、git diff、関連コード、テスト変更、実行結果を確認する。
公開仕様に意図しない変更がないか、テストが緩和されていないか、不要な変更が混ざっていないかを重点確認する。
判定は PASS または FAIL とし、根拠を明記する。
報告形式:
- 判定: PASS / FAIL
- 対象改善ID
- 確認したファイル
- 受入条件ごとの判定
- 公開仕様非変更の確認結果
- 指摘事項
  - 重要度
  - ファイル
  - 箇所
  - 内容
  - 根拠
  - 期待する修正
- 最終判断理由
"""
```

### refactor_tester
用途:
- テスト実行専用
- 本番コード変更禁止
- 失敗原因を分類

作成ファイル:
.codex/agents/refactor_tester.toml

内容:
```toml
name = "refactor_tester"
description = "Test runner agent for refactoring workflows. Runs defined validation commands and classifies failures without changing production code."
sandbox_mode = "workspace-write"
developer_instructions = """
あなたはテスト実行専用エージェントである。
本番コードを変更してはならない。
指定されたテスト、lint、型検査、ビルド、E2E、回帰確認を実行する。
失敗した場合は、製品コード不具合、テストコード不具合、環境要因、外部サービス要因、既存不具合、flaky、原因不明に分類する。
テストを削除、skip、期待値緩和してはならない。
報告では以下を含める。
- 実行コマンド
- 結果
- 失敗ログの要約
- 変更前ベースラインとの差分
- 新規失敗の有無
- 完了判定に使えるか
"""
```

### e2e_probe
用途:
- E2E 環境の調査・起動確認
- UIや統合フローの再現確認
- 本番コード変更禁止

作成ファイル:
.codex/agents/e2e_probe.toml

内容:
```toml
name = "e2e_probe"
description = "E2E environment probe. Detects app startup, browser test tooling, service dependencies, and safe smoke-test commands."
sandbox_mode = "workspace-write"
developer_instructions = """
あなたはE2E環境確認専用エージェントである。
本番コードを変更してはならない。
E2E設定、ブラウザテスト設定、アプリ起動方法、DBや外部サービス依存、Docker依存を調査する。
可能であれば安全な smoke test または test listing を実行する。
認証情報、実サービス、本番データを使ってはならない。
依存関係追加、lockfile更新、CI変更は禁止する。
E2Eが実行できない場合は、理由、必要な前提、低リスクな構築案、代替検証案を報告する。
"""
```

6. 作成後、可能であれば各 agent を実際に spawn し、以下の no-op 検証を行う。
   - refactor_explorer: 「このリポジトリのテスト設定候補を読み取り専用で要約せよ」
   - refactor_reviewer: 「空のdiffに対するレビュー形式を返せ」
   - refactor_tester: 「実行可能な検証コマンド候補を列挙せよ。実行は不要」
   - e2e_probe: 「E2E設定候補を列挙せよ。実行は不要」
7. spawn できた場合は、agent 名、役割、応答要約を記録する。
8. spawn できない場合は、理由を記録し、後続リファクタリング goal で built-in agent または工程分離による代替が必要であることを記録する。

【工程3：検証コマンドの発見】
README、AGENTS、package scripts、Makefile、CI、各種設定から、以下のコマンド候補を特定する。

- install
- dev server 起動
- build
- lint
- format check
- typecheck
- unit test
- integration test
- e2e test
- smoke test
- test listing
- affected test
- CI相当コマンド

推測だけでコマンドを作らない。
候補の根拠となるファイルを必ず記録する。

パッケージマネージャの優先判定:
- pnpm-lock.yaml があれば pnpm
- yarn.lock があれば yarn
- package-lock.json があれば npm
- bun.lockb があれば bun
- uv.lock があれば uv
- poetry.lock があれば poetry
- requirements.txt があれば pip
- Cargo.lock があれば cargo
- go.mod があれば go
- Gemfile.lock があれば bundle
- composer.lock があれば composer

docs/refactoring-preflight-report.md に以下を記録する。

- 検出したコマンド
- 根拠ファイル
- 実行可否
- 実行に必要な前提
- 後続 goal で使うべき限定テスト
- 後続 goal で使うべき最終検証コマンド

【工程4：依存関係と実行環境の確認】
以下を確認する。

- node / npm / pnpm / yarn / bun の有無とバージョン
- python / uv / poetry / pip の有無とバージョン
- ruby / bundle の有無とバージョン
- go の有無とバージョン
- rust / cargo の有無とバージョン
- java / gradle / maven の有無とバージョン
- docker の有無
- docker compose の有無
- ブラウザ実行に必要な Playwright / Cypress / Selenium 等の有無
- DBやqueueなどのローカルサービス依存
- .env.example の有無
- test用環境変数の説明
- 実認証情報が必要か

依存関係が未導入の場合:
- lockfile とドキュメントが存在し、ネットワークアクセスと承認ポリシー上安全な場合のみ install を試みてよい。
- lockfile がない、package manager が不明、ネットワーク不可、または依存追加が必要な場合は install しない。
- install できない場合は BLOCKED ではなく、原因に応じて READY_WITH_LIMITATIONS とするか判断する。

lockfile が変更された場合:
- その変更は原則許可されない。
- 直ちに差分を確認し、意図せず変更された場合は元に戻す。
- 元に戻せない場合は BLOCKED とする。

【工程5：E2E環境の確認】
E2Eについて、以下を順に確認する。

1. E2E設定の有無
   - playwright.config.*
   - cypress.config.*
   - selenium設定
   - webdriver設定
   - detox設定
   - maestro設定
   - rails system test
   - django/pytest browser test
   - CI上のE2E job
   - npm scripts の e2e / test:e2e / cy / playwright
   - Makefile の e2e / smoke / browser test

2. アプリ起動方法
   - dev server
   - preview server
   - test server
   - Docker Compose
   - local DB
   - mock server
   - seed data
   - required env vars

3. E2Eの実行方法
   - test listing
   - dry run
   - smoke test
   - headless browser test
   - targeted spec
   - CI相当コマンド

4. 安全確認
   - 実サービスに接続しないか
   - 本番APIを叩かないか
   - 本番DBを使わないか
   - 認証情報が不要か
   - 外部課金やメール送信が発生しないか
   - テストデータだけで実行できるか

5. 可能な範囲で低コストな確認を実行する。
   優先順位:
   - E2E command の listing
   - E2E framework の version check
   - app server の起動コマンド確認
   - smoke test
   - 1本だけの代表的E2E
   - 全E2Eは重い場合は実行しない

6. E2Eが存在しない場合:
   - すぐに依存関係を追加しない。
   - まず docs/e2e-environment.md に構築案を記録する。
   - 既存のテストフレームワークで軽量 smoke test が作れる場合のみ、補助スクリプト案を作成してよい。
   - package.json 等への依存追加が必要な場合は実装せず、提案に留める。
   - 後続リファクタリング goal では unit / integration / build / typecheck / lint を代替検証とする。

7. E2Eが存在するが動かない場合:
   - 失敗原因を分類する。
   - 依存未導入
   - ブラウザ未導入
   - dev server 起動不可
   - DB未起動
   - Docker不可
   - env不足
   - 外部サービス依存
   - 既存テスト失敗
   - flaky
   - 原因不明

8. E2E構築を行ってよい条件:
   以下をすべて満たす場合のみ、低リスクなE2E環境準備を実施してよい。
   - 既存ドキュメントまたは既存設定に構築手順がある
   - 依存関係追加が不要
   - lockfile変更が不要
   - 本番サービスや秘密情報が不要
   - Dockerやブラウザなどの起動が現環境で許可されている
   - 変更対象が docs/ または scripts/check-e2e-env.* などの補助ファイルに限定される
   - 後続リファクタリングの検証に有用である

【工程6：E2E補助ドキュメントの作成】
E2E環境について、docs/e2e-environment.md を作成または更新する。
既存ファイルがある場合は上書きせず、docs/refactoring-preflight-report.md に既存ファイルへの参照を記録する。

docs/e2e-environment.md には以下を記載する。

# E2E Environment

## 1. E2Eステータス
- READY / READY_WITH_LIMITATIONS / BLOCKED

## 2. 検出したE2Eフレームワーク
- framework
- config file
- command
- root directory

## 3. アプリ起動方法
- command
- required env
- required services
- ports
- DB / queue / cache
- seed data

## 4. 実行可能なE2Eコマンド
- listing
- smoke
- targeted
- full

## 5. 実行結果
- command
- result
- representative log
- failure classification

## 6. 実行できない場合の理由
- missing dependency
- missing browser
- missing service
- missing env
- network restriction
- docker restriction
- external service dependency
- existing test failure
- unknown

## 7. 後続リファクタリング goal での扱い
- E2Eを必須にするか
- 代替検証にするか
- どのコマンドを使うか
- どの失敗を既存失敗として扱うか

【工程7：軽量ベースライン検証】
後続 goal の前提として、可能な範囲で軽量ベースラインを取得する。

優先順位:
1. package manager / language tool version
2. install 状態確認
3. lint または lint listing
4. typecheck
5. unit test の targeted または listing
6. build
7. integration test
8. E2E listing
9. E2E smoke
10. 全E2E

重いコマンド、長時間コマンド、外部サービス依存コマンドは無理に実行しない。
実行しない場合は、理由を記録する。

各コマンドについて以下を docs/refactoring-preflight-report.md に記録する。

- コマンド
- 実行したか
- 実行しなかった場合の理由
- 結果
- 代表ログ
- 既存失敗の有無
- 後続 goal で使うべきか
- 後続 goal で必須にするか任意にするか

【工程8：後続リファクタリング goal 用の実行条件を作成】
docs/refactoring-preflight-report.md に、後続 goal へ渡すための「実行条件」を記載する。

以下を必ず含める。

- 利用可能な subagent
- 実体のある custom subagent を spawn できたか
- spawn できない agent と理由
- built-in agent で代替する必要があるか
- E2Eステータス
- 後続 goal で必ず実行すべきコマンド
- 後続 goal で任意実行にすべきコマンド
- 後続 goal で実行してはいけないコマンド
- 既存失敗
- 環境制約
- 変更禁止領域
- テスト失敗時の既存失敗判定基準
- READY / READY_WITH_LIMITATIONS / BLOCKED の最終判定

【工程9：最終確認】
最終的に git diff --stat と git diff を確認する。

許容される変更:
- docs/refactoring-preflight-report.md
- docs/e2e-environment.md
- .codex/agents/*.toml
- scripts/check-e2e-env.* など明示的な補助ファイル

許容されない変更:
- 本番コード
- テスト期待値
- snapshot
- lockfile
- package manager config
- CI設定
- DB migration
- generated files
- 秘密情報ファイル

許容されない変更がある場合:
- 可能なら元に戻す。
- 元に戻せない場合は BLOCKED とし、理由を記録する。

【docs/refactoring-preflight-report.md の構成】
docs/refactoring-preflight-report.md は日本語で作成する。

# Refactoring Preflight Report

## 1. 概要
- 実施目的
- 最終判定: READY / READY_WITH_LIMITATIONS / BLOCKED
- 後続リファクタリング goal を開始してよいか
- 主な制約

## 2. 作業開始時の状態
- リポジトリルート
- ブランチ
- git status
- 未コミット変更
- 既存変更との衝突可能性

## 3. 確認したプロジェクト情報
- 読んだファイル
- 技術スタック
- パッケージマネージャ
- 起動方法
- テスト構成
- CI構成
- AGENTS.md の重要指示

## 4. Subagent 準備状況
- 既存 custom agents
- 作成した custom agents
- 作成できなかった agents
- spawn 確認結果
- built-in agent で代替する必要性
- subagent 運用上のリスク

## 5. 検証コマンド一覧
各コマンドについて以下を記載する。

- 用途
- コマンド
- 根拠ファイル
- 実行可否
- 実行結果
- 後続 goal での扱い

## 6. E2E 環境確認
- E2E設定の有無
- E2Eフレームワーク
- アプリ起動方法
- 必要なサービス
- 必要な環境変数
- 実行した確認
- 実行結果
- 失敗原因
- E2Eステータス
- 後続 goal でE2Eをどう扱うか

## 7. 軽量ベースライン
- build
- lint
- typecheck
- unit test
- integration test
- e2e listing
- e2e smoke
- 実行結果
- 既存失敗
- 実行不能だったコマンドと理由

## 8. 作成・変更したファイル
- ファイル
- 目的
- 変更理由
- 安全性
- 後続 goal での使い方

## 9. 後続リファクタリング goal への引き継ぎ
- 利用すべき subagent
- 利用すべき検証コマンド
- E2Eの扱い
- 既存失敗の扱い
- 変更禁止領域
- 注意すべき環境制約
- READY_WITH_LIMITATIONS の場合の制限
- BLOCKED の場合に解消すべき事項

## 10. 最終判定
- READY / READY_WITH_LIMITATIONS / BLOCKED
- 判定理由
- 後続 goal を開始してよいか
- 開始する場合の条件
- 開始すべきでない場合の理由

【完了条件】
以下を満たした場合に完了とする。

- docs/refactoring-preflight-report.md が作成されている。
- subagent の利用可否が記録されている。
- 必要に応じて .codex/agents/*.toml が作成されている。
- custom subagent を spawn できたか、できない場合は理由が記録されている。
- E2E環境の有無と実行可否が記録されている。
- E2Eを実行できない場合、その理由と代替検証方針が記録されている。
- build / lint / typecheck / test / e2e の候補コマンドが整理されている。
- 軽量ベースラインが可能な範囲で取得されている。
- 後続リファクタリング goal に渡す制約が明確になっている。
- 最終判定が READY / READY_WITH_LIMITATIONS / BLOCKED のいずれかで明記されている。
- 許可されていないファイルに差分がない。

【最終出力】
最後に、以下を簡潔に報告する。

- 最終判定: READY / READY_WITH_LIMITATIONS / BLOCKED
- 作成した subagent
- subagent spawn 確認結果
- E2Eステータス
- 実行した主要コマンド
- 既存失敗
- 後続リファクタリング goal を開始してよいか
- 開始する場合の条件
- docs/refactoring-preflight-report.md の作成結果
