# Google Drive Sync

Drive sync uploads Markdown reports to personal My Drive using OAuth.

## Destination

The app creates and manages this folder tree:

```text
My Drive/
  AI Researcher/
    daily/
    weekly/
    monthly/
    evolution/
```

The root folder name is configured in `config/sources.json`:

```json
{
  "integrations": {
    "google_drive": {
      "enabled": true,
      "root_folder_name": "AI Researcher"
    }
  }
}
```

## Sync Timing

Report generation writes the local Markdown file first. Then Drive sync runs. Then Discord notification runs with the Drive URL when sync succeeds.

If Drive sync fails, the report still exists locally and the pipeline continues unless `fail_report_on_error` is set to `true`.

## Manual Sync

Sync all existing reports:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli drive-sync
```

Sync one report:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli drive-sync --path reports/daily/2026-06-05.md
```

If the path is not under `reports/<period>/`, pass period and label explicitly:

```bash
docker compose run --rm ai-researcher \
  python3 -m ai_researcher.cli drive-sync \
  --path /app/reports/custom.md --period daily --label custom
```

## Database Tracking

Drive sync state is stored in SQLite:

```text
drive_files
```

It records local path, period, label, Drive file ID, Drive URL, MIME type, sync status, sync time, and metadata.

## File Format

Reports are uploaded as Markdown:

```text
text/markdown
```

Google Docs conversion is intentionally not enabled by default. Markdown keeps the local and Drive copies identical.
