# Docker Operation

The production path is Docker Compose. The `ai-researcher` service runs `supercronic` with `config/crontab`, which starts `scripts/run_scheduled.sh` every day at 09:00 Asia/Tokyo. The optional `ai-researcher-discord-bot` service is behind the `discord-bot` profile. It uses the same image and keeps a Discord Gateway connection open for article feedback interactions.

## Files and Volumes

Host paths:

```text
./data      -> /app/data
./reports   -> /app/reports
./logs      -> /app/logs
./secrets   -> /secrets
```

Required secret files:

```text
secrets/google-service-account.json
secrets/google-drive-oauth-client.json
```

Generated secret file:

```text
secrets/google-drive-token.json
```

## Environment

Copy `.env.example`:

```bash
cp .env.example .env
```

Set at least:

```env
GOOGLE_CLOUD_PROJECT=your-google-cloud-project-id
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.5-flash
GEMINI_GROUNDING_MODEL=gemini-3.5-flash
```

Optional:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_BOT_TOKEN=...
DISCORD_CHANNEL_ID=...
DISCORD_ALLOWED_USER_IDS=123456789012345678,234567890123456789
QIITA_TOKEN=...
```

If `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID` are set, report generation posts article-by-article feedback cards through the bot. If they are empty, the existing webhook notification path is used.

## First Start

Authorize Drive once:

```bash
docker compose run --rm -p 8080:8080 ai-researcher \
  python3 -m ai_researcher.cli drive-auth --port 8080
```

Start the scheduler:

```bash
docker compose up -d --build
```

Start the Discord feedback bot as well:

```bash
docker compose --profile discord-bot up -d --build ai-researcher ai-researcher-discord-bot
```

Run once immediately:

```bash
docker compose run --rm ai-researcher bash scripts/run_scheduled.sh
```

## Logs

```bash
docker compose logs -f ai-researcher ai-researcher-discord-bot
```

The scheduled job is protected by `flock` at `data/ai-researcher.lock`. If a previous run is still active, the next run is skipped.

## Gemini Search Grounding

The same service account and Vertex AI API are used for:

```text
report selection
selected article verification
weekly/monthly source discovery
```

Set `GEMINI_GROUNDING_MODEL` only when you want Search Grounding to use a different Gemini model than report selection. Source discovery runs on Monday and on the first day of the month inside the normal 09:00 scheduled workflow.

## Upgrade

```bash
docker compose down
docker compose up -d --build
```

Keep `data/`, `reports/`, and `secrets/` backed up before moving the service to another machine.
On a VPS, keep both Compose services on the same mounted `data/` directory so scheduled reports and Discord feedback write to the same SQLite database.
