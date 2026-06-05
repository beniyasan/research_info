# AI Researcher

AI Researcher は、Zenn、Qiita、RSS、arXiv、Hacker News、海外テックメディアからAI関連の記事を収集し、Geminiでレポート採用記事を選定する情報収集・レポート生成ツールです。採用実績をSQLiteに記録し、単なる収集数ではなく「最終レポートへの貢献度」をもとにキーワードやソースを進化させます。

## 現在の構成

- スケジューラー: Docker + supercronic
- LLM選定: Vertex AI `gemini-3.5-flash`
- Web検証・発見: Gemini Search Grounding
- レポート保存: ローカルMarkdown + SQLite
- クラウド同期: Google Drive OAuth + 個人の My Drive
- 通知: Discord Webhook

Dockerの標準スケジュールは、Asia/Tokyoで毎日09:00に1回です。

- 日次レポート: 前日分
- 週次レポート: 月曜日のみ、前週分
- 月次レポート: 毎月1日のみ、前月分

## クイックスタート

実行用ディレクトリを作成します。

```bash
mkdir -p secrets data reports logs
cp .env.example .env
```

`secrets/` に以下のファイルを配置します。

```text
secrets/google-service-account.json
secrets/google-drive-oauth-client.json
```

`.env` を編集します。

```env
GOOGLE_CLOUD_PROJECT=your-google-cloud-project-id
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.5-flash
GEMINI_GROUNDING_MODEL=gemini-3.5-flash
DISCORD_WEBHOOK_URL=
QIITA_TOKEN=
```

Google Driveを初回認証します。

```bash
docker compose run --rm -p 8080:8080 ai-researcher \
  python3 -m ai_researcher.cli drive-auth --port 8080
```

表示されたURLをブラウザで開き、Googleアカウントで許可します。成功するとrefresh tokenが以下に保存されます。

```text
secrets/google-drive-token.json
```

常駐スケジューラーを起動します。

```bash
docker compose up -d --build
```

すぐに1回実行する場合:

```bash
docker compose run --rm ai-researcher bash scripts/run_scheduled.sh
```

ログ確認:

```bash
docker compose logs -f ai-researcher
```

## 出力先

ローカルレポート:

```text
reports/daily/
reports/weekly/
reports/monthly/
reports/evolution/
```

Google Drive:

```text
My Drive/
  AI Researcher/
    daily/
    weekly/
    monthly/
    evolution/
```

SQLiteデータベース:

```text
data/research.db
```

収集した生データ:

```text
data/raw/YYYY-MM-DD/articles.json
```

Groundingの履歴:

```text
source_discoveries
article_verifications
```

## 巡回ソース

現在は以下を巡回します。

- 国内技術コミュニティ: Zenn、Qiita
- 公式・ベンダー: OpenAI、Anthropic、Google AI、Google DeepMind、Hugging Face、Microsoft AI、Meta AI、Mistral AI、NVIDIA、AWS
- 開発者コミュニティ: GitHub Blog AI/ML、LangChain、LlamaIndex、Hacker News
- 研究: arXiv cs.AI、arXiv cs.CL、Microsoft Research
- 海外ニュース・分析: TechCrunch AI、VentureBeat AI、MIT Technology Review AI
- ニュースレター: Import AI、The Batch
- スライド: Speaker DeckのTechnology、Programming、Research、Scienceカテゴリ

## よく使うコマンド

Google Drive認証だけ実行:

```bash
docker compose run --rm -p 8080:8080 ai-researcher \
  python3 -m ai_researcher.cli drive-auth --port 8080
```

既存レポートをGoogle Driveへ同期:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli drive-sync
```

特定のレポートだけ同期:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli drive-sync --path reports/daily/2026-06-05.md
```

Discord通知テスト:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli notify-test
```

Gemini Searchでソース候補を手動発見:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli discover-sources --cadence manual
```

週次・月次も強制生成:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli run-scheduled --force-weekly --force-monthly
```

## Google認証の考え方

このプロジェクトでは、Google認証を2種類使います。

```text
Gemini / Vertex AI
  - サービスアカウント
  - secrets/google-service-account.json

Google Drive / My Drive
  - OAuth
  - secrets/google-drive-oauth-client.json
  - secrets/google-drive-token.json
```

Gemini用のサービスアカウントでは、個人のMy Driveには自然に保存できません。そのためDrive同期は、あなたのGoogleアカウントで初回OAuth認可し、refresh tokenを保存して自動実行します。

Drive APIのスコープは最小権限の `drive.file` を使います。

```text
https://www.googleapis.com/auth/drive.file
```

OAuth同意画面がTestingのままだと、Driveスコープのrefresh tokenが7日で切れることがあります。常駐運用する場合は、OAuthアプリをIn productionにしておくのが安全です。

## レポート生成の流れ

毎朝09:00のジョブでは、以下の順に処理します。

```text
1. 設定済みソースから記事収集
2. スコアリング
3. Gemini 3.5 Flashで採用記事を選定
4. Gemini Search Groundingで採用記事を検証
5. Markdownレポート生成
6. Google Driveへ同期
7. Discordへ通知
8. 月曜・毎月1日はGemini Searchで新規ソース候補を発見
9. 採用実績をもとにキーワード・ソースを進化
```

海外記事については、原文タイトル・原文要約を残しつつ、Geminiが生成した日本語タイトル・日本語要約も併記します。

記事検証はレポート本文の上位記事に `Web verification` として表示されます。ソース発見は検索結果を即時本採用せず、RSS/Atomとして取得できるものだけ `candidate` ソースに入れます。その後は通常の収集・採用実績によって昇格または停止されます。

## 主要な設定ファイル

- `config/sources.json`: 収集ソース、キーワード、Gemini、Drive、Discord設定
- `config/crontab`: Docker内スケジュール
- `.env`: Dockerに渡す環境変数
- `docker-compose.yml`: 常駐コンテナ定義

## 詳細ドキュメント

- [Docker運用](docs/docker.md)
- [Google認証](docs/google-auth.md)
- [Google Drive同期](docs/drive-sync.md)
- [運用・トラブルシュート](docs/operations.md)

## 注意点

- Slackや社内データは使いません。公開情報のみを対象にします。
- Google Drive同期に失敗しても、ローカルレポート生成は継続します。
- Discord通知には、Drive同期が成功した場合のみDrive URLが含まれます。
- Docker実行環境では `secrets/`、`data/`、`reports/` をバックアップ対象にしてください。
