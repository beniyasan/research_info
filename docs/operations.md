# Operations and Troubleshooting

## Scheduler Behavior

`config/crontab` runs:

```cron
0 9 * * * /app/scripts/run_scheduled.sh
```

`run-scheduled` uses the local date in `AI_RESEARCH_TZ`, defaulting to `Asia/Tokyo`.

At 09:00:

```text
collect
daily report for yesterday
weekly report for previous week, only on Monday
monthly report for previous month, only on day 1
selected article verification with Gemini Search Grounding
source discovery with Gemini Search Grounding, only on Monday and day 1
evolve
```

## Manual Runs

Run the scheduled workflow:

```bash
docker compose run --rm ai-researcher bash scripts/run_scheduled.sh
```

Force weekly and monthly reports:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli run-scheduled --force-weekly --force-monthly
```

Run for a specific scheduler date:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli run-scheduled --date 2026-07-01
```

Discover candidate sources manually:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli discover-sources --cadence manual
```

## Discord

Set:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Test:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli notify-test
```

Discord notifications include the Drive report URL when Drive sync succeeds.

## Common Failures

`Google Drive OAuth token is missing`

Run:

```bash
docker compose run --rm -p 8080:8080 ai-researcher \
  python3 -m ai_researcher.cli drive-auth --port 8080
```

`Vertex AI Gemini request failed`

Check:

```text
GOOGLE_CLOUD_PROJECT
GOOGLE_CLOUD_LOCATION
secrets/google-service-account.json
Vertex AI API enabled
service account permissions
```

`Gemini grounding request failed`

Check the same Vertex AI settings as above. Article verification and source discovery are configured to record failure and continue by default unless `fail_report_on_error` or `fail_run_on_error` is set to `true`.

`Refresh token expired`

Rerun `drive-auth`. If this happens every 7 days, check that the OAuth consent screen is not left in Testing status.

`database is locked`

The scheduled Docker path uses `flock`. Avoid starting multiple manual runs at the same time.
