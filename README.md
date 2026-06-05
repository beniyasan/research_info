# AI Researcher

AI Researcher collects AI-related public articles from Zenn, Qiita, RSS feeds, arXiv, Hacker News, and overseas tech media. It scores candidates, asks Gemini to select useful articles for Japanese reports, records adoption history in SQLite, evolves keywords/sources from actual report contribution, notifies Discord, and syncs Markdown reports to Google Drive.

## Current Stack

- Scheduler: Docker + supercronic
- LLM selector: Vertex AI `gemini-3.5-flash`
- Web verification/discovery: Gemini Search Grounding
- Report storage: local Markdown + SQLite
- Cloud sync: Google Drive OAuth to personal My Drive
- Notification: Discord webhook

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

## Documentation

- [Docker operation](docs/docker.md)
- [Google authentication](docs/google-auth.md)
- [Google Drive sync](docs/drive-sync.md)
- [Operations and troubleshooting](docs/operations.md)

## Notes

- This is a public-source-only implementation. No Slack or internal data is used.
- For non-Japanese articles, reports keep the original title/summary and also show Gemini's Japanese title/summary.
- Selected articles are verified with Gemini Search Grounding before Markdown/Discord output.
- Source discovery stores search results as candidates first; validated feeds enter `sources.status=candidate` and still need adoption history before promotion.
- Final source/keyword evolution is based on report adoption history, not just collection volume.
