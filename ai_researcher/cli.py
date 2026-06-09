from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, db
from .collector import collect
from .config import DEFAULT_CONFIG_PATH, load_config, seed_from_config
from .drive_sync import authorize_drive, sync_existing_reports, sync_one_report
from .evolver import evolve
from .notifier import notify_test
from .preferences import feedback_summary
from .reporter import generate_report
from .source_discovery import discover_sources


DEFAULT_DB_PATH = "data/research.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-researcher")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Source config JSON path")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize the SQLite database")
    sub.add_parser("seed", help="Seed sources and keywords from config")
    sub.add_parser("collect", help="Collect articles from configured sources")

    report = sub.add_parser("report", help="Generate a Markdown report")
    report.add_argument("--period", choices=["daily", "weekly", "monthly"], default="daily")
    report.add_argument("--date", help="Report date in YYYY-MM-DD, interpreted in AI_RESEARCH_TZ")

    sub.add_parser("evolve", help="Promote/demote keywords, sources, and candidates")
    sub.add_parser("notify-test", help="Send a Discord test notification")
    sub.add_parser("discord-bot", help="Run the Discord feedback Gateway bot")
    sub.add_parser("feedback-summary", help="Summarize stored Discord article feedback")

    drive_auth = sub.add_parser("drive-auth", help="Authorize Google Drive OAuth for My Drive sync")
    drive_auth.add_argument("--port", type=int, default=8080)
    drive_auth.add_argument("--bind-host", default="0.0.0.0")

    drive_sync = sub.add_parser("drive-sync", help="Sync existing Markdown reports to Google Drive")
    drive_sync.add_argument("--path", help="Specific report path to sync")
    drive_sync.add_argument("--period", choices=["daily", "weekly", "monthly", "evolution"])
    drive_sync.add_argument("--label", help="Report label, defaults to the file stem")

    discover = sub.add_parser("discover-sources", help="Discover candidate sources with Gemini Search grounding")
    discover.add_argument("--cadence", choices=["weekly", "monthly", "manual"], default="manual")
    discover.add_argument("--limit", type=int, help="Maximum discovered sources to request")

    run_daily = sub.add_parser("run-daily", help="Run init, seed, collect, daily report, and evolve")
    run_daily.add_argument("--date", help="Report date in YYYY-MM-DD, interpreted in AI_RESEARCH_TZ")

    run_scheduled = sub.add_parser("run-scheduled", help="Run the daily scheduled workflow")
    run_scheduled.add_argument("--date", help="Scheduler date in YYYY-MM-DD, defaults to today in AI_RESEARCH_TZ")
    run_scheduled.add_argument("--report-date", help="Report date, defaults to the previous local day")
    run_scheduled.add_argument("--force-weekly", action="store_true", help="Generate the weekly report even if not Monday")
    run_scheduled.add_argument("--force-monthly", action="store_true", help="Generate the monthly report even if not the first day")

    args = parser.parse_args(argv)
    conn = db.connect(args.db)

    if args.command == "init":
        db.init_db(conn)
        print_json({"status": "ok", "db": args.db})
        return 0

    config = load_config(args.config)

    if args.command == "seed":
        db.init_db(conn)
        print_json(seed_from_config(conn, config))
        return 0

    if args.command == "collect":
        db.init_db(conn)
        seed_from_config(conn, config)
        print_json(collect(conn, config))
        return 0

    if args.command == "report":
        db.init_db(conn)
        result = generate_report(conn, config, period=args.period, report_date=args.date)
        print_json(result)
        return 0

    if args.command == "evolve":
        db.init_db(conn)
        result = evolve(conn, config)
        print_json(result)
        return 0

    if args.command == "notify-test":
        print_json(notify_test(config))
        return 0

    if args.command == "discord-bot":
        db.init_db(conn)
        from .discord_bot import run_discord_bot

        run_discord_bot(args.db)
        return 0

    if args.command == "feedback-summary":
        db.init_db(conn)
        print_json(feedback_summary(conn))
        return 0

    if args.command == "drive-auth":
        print_json(authorize_drive(config, port=args.port, bind_host=args.bind_host))
        return 0

    if args.command == "drive-sync":
        db.init_db(conn)
        if args.path:
            result = sync_one_report(conn, config, report_path=args.path, period=args.period, label=args.label)
        else:
            result = sync_existing_reports(conn, config)
        print_json(result)
        return 0

    if args.command == "discover-sources":
        db.init_db(conn)
        seed_from_config(conn, config)
        print_json(discover_sources(conn, config, cadence=args.cadence, limit=args.limit))
        return 0

    if args.command == "run-daily":
        db.init_db(conn)
        seed = seed_from_config(conn, config)
        collected = collect(conn, config)
        reported = generate_report(conn, config, period="daily", report_date=args.date)
        evolved = evolve(conn, config)
        print_json({"seed": seed, "collect": collected, "report": reported, "evolve": evolved})
        return 0

    if args.command == "run-scheduled":
        db.init_db(conn)
        today = _scheduler_date(args.date)
        report_date = date.fromisoformat(args.report_date) if args.report_date else today - timedelta(days=1)
        seed = seed_from_config(conn, config)
        collected = collect(conn, config)

        reports = {
            "daily": generate_report(conn, config, period="daily", report_date=report_date.isoformat()),
        }
        if args.force_weekly or today.weekday() == 0:
            reports["weekly"] = generate_report(conn, config, period="weekly", report_date=report_date.isoformat())
        else:
            reports["weekly"] = {"status": "skipped", "reason": "not Monday"}

        if args.force_monthly or today.day == 1:
            reports["monthly"] = generate_report(conn, config, period="monthly", report_date=report_date.isoformat())
        else:
            reports["monthly"] = {"status": "skipped", "reason": "not first day of month"}

        discoveries = {"weekly": {"status": "skipped", "reason": "not Monday"}, "monthly": {"status": "skipped", "reason": "not first day of month"}}
        if args.force_weekly or today.weekday() == 0:
            discoveries["weekly"] = discover_sources(conn, config, cadence="weekly")
        if args.force_monthly or today.day == 1:
            discoveries["monthly"] = discover_sources(conn, config, cadence="monthly")

        evolved = evolve(conn, config)
        print_json(
            {
                "scheduler_date": today.isoformat(),
                "report_date": report_date.isoformat(),
                "seed": seed,
                "collect": collected,
                "reports": reports,
                "discoveries": discoveries,
                "evolve": evolved,
            }
        )
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")


def _scheduler_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    tz = ZoneInfo(os.environ.get("AI_RESEARCH_TZ", "Asia/Tokyo"))
    return datetime.now(tz).date()


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
