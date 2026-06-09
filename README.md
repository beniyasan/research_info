# AI Researcher

AI Researcher collects AI-related public articles from Zenn, Qiita, RSS feeds, arXiv, Hacker News, and overseas tech media. It scores candidates, asks Gemini to select useful articles for Japanese reports, records adoption history in SQLite, evolves keywords/sources from actual report contribution, notifies Discord, and syncs Markdown reports to Google Drive.

## Current Stack

- Scheduler: Docker + supercronic
- LLM selector: Vertex AI `gemini-3.5-flash`
- Web verification/discovery: Gemini Search Grounding
- Report storage: local Markdown + SQLite
- Cloud sync: Google Drive OAuth to personal My Drive
- Notification: Discord webhook / Discord bot feedback cards

The default Docker schedule runs once every day at 09:00 Asia/Tokyo:

- daily report: previous day
- weekly report: previous week, only on Monday
- monthly report: previous month, only on the first day of the month

## Quick Start

Create the local runtime folders:

```bash
mkdir -p secrets data reports logs
cp .env.example .env
```

Place these files under `secrets/`:

```text
secrets/google-service-account.json
secrets/google-drive-oauth-client.json
```

Edit `.env`:

```env
GOOGLE_CLOUD_PROJECT=your-google-cloud-project-id
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.5-flash
GEMINI_GROUNDING_MODEL=gemini-3.5-flash
DISCORD_WEBHOOK_URL=
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
DISCORD_ALLOWED_USER_IDS=
QIITA_TOKEN=
```

Authorize Google Drive once:

```bash
docker compose run --rm -p 8080:8080 ai-researcher \
  python3 -m ai_researcher.cli drive-auth --port 8080
```

Open the printed URL in your browser and approve access. The refresh token is saved to:

```text
secrets/google-drive-token.json
```

Start the always-on scheduler:

```bash
docker compose up -d --build
```

Run the full scheduled workflow once immediately:

```bash
docker compose run --rm ai-researcher bash scripts/run_scheduled.sh
```

Watch logs:

```bash
docker compose logs -f ai-researcher
```

## Outputs

Local reports:

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

SQLite database:

```text
data/research.db
```

Raw fetched articles:

```text
data/raw/YYYY-MM-DD/articles.json
```

Grounding history:

```text
source_discoveries
article_verifications
```

Discord feedback history:

```text
article_feedback
article_feedback_events
```

Google Drive sync state:

```text
drive_files
```

## Source Coverage

The default configuration watches:

- Japan tech community: Zenn and Qiita
- Official/vendor sources: OpenAI, Anthropic, Google AI, Google DeepMind, Hugging Face, Microsoft AI, Meta AI, Mistral AI, NVIDIA, and AWS
- Developer community: GitHub Blog AI/ML, LangChain, LlamaIndex, and Hacker News
- Research: arXiv cs.AI, arXiv cs.CL, and Microsoft Research
- Overseas news and analysis: TechCrunch AI, VentureBeat AI, and MIT Technology Review AI
- Newsletters: Import AI and The Batch
- Slides: Speaker Deck Technology, Programming, Research, and Science categories

## Common Commands

Run only Google Drive authorization:

```bash
docker compose run --rm -p 8080:8080 ai-researcher \
  python3 -m ai_researcher.cli drive-auth --port 8080
```

Sync existing reports to Drive:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli drive-sync
```

Generate and sync one report path:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli drive-sync --path reports/daily/2026-06-05.md
```

Send a Discord test notification:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli notify-test
```

Start the Discord feedback bot:

```bash
docker compose --profile discord-bot up -d ai-researcher-discord-bot
```

Summarize saved feedback:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli feedback-summary
```

Discover candidate sources with Gemini Search:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli discover-sources --cadence manual
```

Force weekly and monthly reports:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli run-scheduled --force-weekly --force-monthly
```

## Google Authentication Model

This project uses two separate Google authentication flows:

```text
Gemini / Vertex AI
  - service account
  - secrets/google-service-account.json

Google Drive / My Drive
  - OAuth
  - secrets/google-drive-oauth-client.json
  - secrets/google-drive-token.json
```

The Gemini service account is used for report selection and Gemini Search Grounding. It does not naturally write to a personal My Drive, so Drive sync is authorized once through your Google account and then reuses the saved refresh token.

Drive sync uses the least-privilege `drive.file` scope:

```text
https://www.googleapis.com/auth/drive.file
```

If the OAuth consent screen remains in Testing status, Drive refresh tokens can expire after 7 days. For always-on operation, move the OAuth app to In production after confirming the consent settings.

## Report Workflow

The 09:00 scheduled job runs these steps:

```text
1. Collect articles from configured public sources
2. Score candidates
3. Ask Gemini 3.5 Flash to select report articles
4. Verify selected articles with Gemini Search Grounding
5. Write Markdown reports
6. Sync reports to Google Drive
7. Notify Discord
8. Discover new source candidates on Mondays and on the first day of the month
9. Evolve keywords and sources from adoption history
```

When `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID` are set, report generation posts one Discord feedback card per selected article. Each card can store one of `刺さる`, `追う`, `既知`, `弱い`, or `方向違い`, plus a free-form comment. Without bot settings, notifications fall back to `DISCORD_WEBHOOK_URL`.

Feedback does not immediately dominate selection. It is stored in SQLite first, becomes active after at least 20 feedback items, and applies a capped per-article adjustment of ±0.7 so the candidate pool can still include adjacent or not-yet-articulated interests.

Non-Japanese articles keep their original title and summary while also showing Gemini-generated Japanese title and summary.

Article verification appears in reports as `Web verification`. Source discovery stores search results as candidates first; only validated RSS or Atom feeds enter `sources.status=candidate`, and later promotion still depends on normal collection and adoption history.

## Key Configuration Files

- `config/sources.json`: sources, keywords, Gemini, Drive, Discord, Grounding, and preference settings
- `config/crontab`: Docker-internal supercronic schedule
- `.env`: environment variables passed to Docker Compose
- `docker-compose.yml`: scheduler and optional Discord feedback bot services
- `scripts/run_scheduled.sh`: locked scheduled runner using `AI_RESEARCH_DB`, `AI_RESEARCH_CONFIG`, and `AI_RESEARCH_LOCK_FILE` overrides

## Documentation

- [Docker operation](docs/docker.md)
- [Google authentication](docs/google-auth.md)
- [Google Drive sync](docs/drive-sync.md)
- [Operations and troubleshooting](docs/operations.md)

## Notes

- This is a public-source-only implementation. No Slack or internal data is used.
- Google Drive sync failure does not stop local report generation unless `fail_report_on_error` is set to `true`.
- Discord notifications include the Drive URL only when Drive sync succeeds.
- Back up `secrets/`, `data/`, and `reports/` for Docker operation.
- For non-Japanese articles, reports keep the original title/summary and also show Gemini's Japanese title/summary.
- Selected articles are verified with Gemini Search Grounding before Markdown/Discord output.
- When `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID` are set, selected articles are posted as individual Discord feedback cards. Without bot settings, the existing `DISCORD_WEBHOOK_URL` path is preserved.
- Feedback is stored first and only becomes a weak selection signal after 20 feedback items. The per-article adjustment is capped at ±0.7 to keep adjacent and not-yet-articulated interests in the candidate pool.
- Source discovery stores search results as candidates first; validated feeds enter `sources.status=candidate` and still need adoption history before promotion.
- Final source/keyword evolution is based on report adoption history, not just collection volume.
