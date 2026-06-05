# Docker Operation

The production path is Docker Compose. The container runs `supercronic` with `config/crontab`, which starts `scripts/run_scheduled.sh` every day at 09:00 Asia/Tokyo.

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
QIITA_TOKEN=...
```

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

Run once immediately:

```bash
docker compose run --rm ai-researcher bash scripts/run_scheduled.sh
```

## Logs

```bash
docker compose logs -f ai-researcher
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
