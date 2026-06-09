from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import db
from .article_verifier import verify_selected_articles
from .drive_sync import sync_report
from .llm_selector import select_report_articles
from .notifier import notify_report


PERIOD_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 31,
}


def generate_report(
    conn,
    config: dict[str, Any],
    *,
    period: str = "daily",
    report_date: str | None = None,
    output_dir: str | Path = "reports",
) -> dict[str, Any]:
    if period not in PERIOD_DAYS:
        raise ValueError(f"Unsupported period: {period}")

    threshold = float(config.get("reporting", {}).get("score_threshold", 2.5))
    max_articles = int(config.get("reporting", {}).get("max_articles", 30))
    candidate_limit = int(config.get("selection", {}).get("candidate_limit", max_articles * 3))
    start_utc, end_utc, label = _period_bounds(period, report_date)
    run_id = db.begin_run(conn, f"report:{period}", {"start": start_utc, "end": end_utc})

    try:
        rows = _fetch_report_articles(conn, start_utc, end_utc, threshold)
        candidates = _dedupe(rows)[:candidate_limit]
        selection = select_report_articles(
            candidates,
            config,
            period=period,
            label=label,
            max_articles=max_articles,
            conn=conn,
        )
        selected = selection["selected"]
        report_key = f"{period}:{label}"
        newly_adopted = db.mark_articles_adopted(conn, selected, report_key=report_key)
        verification = verify_selected_articles(
            conn,
            config,
            selected,
            report_key=report_key,
            period=period,
            label=label,
        )
        conn.commit()

        path = Path(output_dir) / period / f"{label}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        verification_items = {item["article_id"]: item for item in verification.get("items", [])}
        path.write_text(
            _render_markdown(conn, selected, period, label, start_utc, end_utc, verification_items),
            encoding="utf-8",
        )

        details = {
            "path": str(path),
            "candidates": len(rows),
            "selected": len(selected),
            "newly_adopted": newly_adopted,
            "selection": {
                "method": selection["method"],
                "status": selection["status"],
                "reason": selection.get("reason"),
            },
            "verification": _verification_details(verification),
        }
        details["drive"] = sync_report(conn, config, report_path=path, period=period, label=label)
        details["notification"] = notify_report(
            selected,
            period=period,
            label=label,
            report_path=path,
            candidates=len(rows),
            selected=len(selected),
            newly_adopted=newly_adopted,
            config=config,
            drive=details["drive"],
            verification=details["verification"],
            report_key=report_key,
        )
        db.finish_run(conn, run_id, "ok", details)
        return details
    except Exception as exc:
        conn.rollback()
        db.finish_run(conn, run_id, "failed", {"error": str(exc)})
        raise


def _fetch_report_articles(conn, start_utc: str, end_utc: str, threshold: float):
    return list(
        conn.execute(
            """
            SELECT
              a.*,
              s.name AS source_name,
              s.category AS source_category,
              s.priority AS source_priority
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE COALESCE(a.published_at, a.fetched_at) >= ?
              AND COALESCE(a.published_at, a.fetched_at) < ?
              AND a.score >= ?
            ORDER BY a.score DESC, COALESCE(a.published_at, a.fetched_at) DESC
            """,
            (start_utc, end_utc, threshold),
        )
    )


def _dedupe(rows):
    best_by_cluster = {}
    for row in rows:
        key = row["cluster_key"]
        current = best_by_cluster.get(key)
        if current is None or row["score"] > current["score"]:
            best_by_cluster[key] = row
    return sorted(best_by_cluster.values(), key=lambda row: (row["score"], row["source_priority"]), reverse=True)


def _render_markdown(
    conn,
    rows,
    period: str,
    label: str,
    start_utc: str,
    end_utc: str,
    verifications: dict[str, dict[str, Any]] | None = None,
) -> str:
    title = {
        "daily": "Daily AI Research Report",
        "weekly": "Weekly AI Research Report",
        "monthly": "Monthly AI Research Report",
    }[period]
    lines = [
        f"# {title}: {label}",
        "",
        f"- Period UTC: `{start_utc}` - `{end_utc}`",
        f"- Selected articles: {len(rows)}",
        "",
    ]

    if not rows:
        lines.extend(["No relevant articles found.", ""])
        return "\n".join(lines)

    lines.extend(["## Executive Picks", ""])
    for row in rows[:10]:
        lines.extend(_article_lines(row, (verifications or {}).get(row["id"])))

    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[_section_name(row["source_category"])].append(row)

    for section, section_rows in grouped.items():
        lines.extend([f"## {section}", ""])
        for row in section_rows[:12]:
            display_title = row["translated_title"] or row["title"]
            lines.append(f"- [{display_title}]({row['url']})")
            lines.append(f"  - Source: {row['source_name']} | Score: {row['score']:.2f}")
        lines.append("")

    lines.extend(_source_health(conn))
    lines.extend(_candidate_lines(conn))
    return "\n".join(lines)


def _article_lines(row, verification: dict[str, Any] | None = None) -> list[str]:
    reasons = ", ".join(json.loads(row["reasons"] or "[]")[:4])
    keywords = ", ".join(json.loads(row["matched_keywords"] or "[]")[:8])
    original_title = row["title"]
    original_summary = _clean_summary(row["summary"])
    translated_title = row["translated_title"]
    translated_summary = _clean_summary(row["translated_summary"])
    is_non_japanese = not _contains_japanese(original_title)
    display_title = translated_title or original_title

    lines = [
        f"### [{display_title}]({row['url']})",
        "",
        f"- Source: {row['source_name']} ({row['source_category']})",
        f"- Score: {row['score']:.2f}",
    ]
    if translated_title and translated_title != original_title:
        lines.append(f"- Japanese title: {translated_title}")
    if is_non_japanese:
        lines.append(f"- Original title: {original_title}")
    if row["selection_method"]:
        lines.append(f"- Selection: {row['selection_method']}")
    if row["selection_reason"]:
        lines.append(f"- Selection reason: {row['selection_reason']}")
    if keywords:
        lines.append(f"- Keywords: {keywords}")
    if reasons:
        lines.append(f"- Reasons: {reasons}")
    if verification:
        lines.extend(_verification_lines(verification))
    if translated_summary:
        lines.append(f"- Japanese summary: {_truncate(translated_summary, 360)}")
    if original_summary:
        label = "Original summary" if is_non_japanese else "Summary"
        if not translated_summary or is_non_japanese:
            lines.append(f"- {label}: {_truncate(original_summary, 360)}")
    lines.append("")
    return lines


def _verification_lines(verification: dict[str, Any]) -> list[str]:
    status = verification.get("status") or "needs_review"
    summary = _clean_summary(verification.get("verification_summary"))
    lines = []
    if summary:
        lines.append(f"- Web verification: {status} - {_truncate(summary, 260)}")
    else:
        lines.append(f"- Web verification: {status}")
    primary_url = verification.get("primary_source_url")
    primary_title = verification.get("primary_source_title") or primary_url
    if primary_url:
        lines.append(f"- Primary source: [{primary_title}]({primary_url})")
    return lines


def _verification_details(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in verification.items()
        if key != "items"
    }


def _clean_summary(value: str | None) -> str:
    return (value or "").strip().replace("\n", " ")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _contains_japanese(value: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u9fff"
        for char in value
    )


def _source_health(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT name, status, hit_count, adopted_count, last_seen_at, last_adopted_at
        FROM sources
        ORDER BY adopted_count DESC, hit_count DESC
        LIMIT 20
        """
    ).fetchall()
    lines = ["## Source Health", ""]
    for row in rows:
        lines.append(
            f"- {row['name']}: status={row['status']}, hits={row['hit_count']}, "
            f"adopted={row['adopted_count']}, last_adopted={row['last_adopted_at'] or '-'}"
        )
    lines.append("")
    return lines


def _candidate_lines(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT kind, value, status, hit_count, adopted_count
        FROM candidates
        WHERE status IN ('candidate', 'active')
        ORDER BY adopted_count DESC, hit_count DESC
        LIMIT 25
        """
    ).fetchall()
    lines = ["## Evolving Candidates", ""]
    if not rows:
        lines.extend(["No candidates yet.", ""])
        return lines
    for row in rows:
        lines.append(
            f"- {row['kind']} `{row['value']}`: status={row['status']}, "
            f"hits={row['hit_count']}, adopted={row['adopted_count']}"
        )
    lines.append("")
    return lines


def _section_name(category: str) -> str:
    return {
        "jp-tech": "Japan Tech Community",
        "official": "Official Vendor Updates",
        "research": "Research",
        "dev-community": "Developer Community",
        "news": "News and Analysis",
        "newsletter": "Newsletters",
        "slides": "Slides and Presentations",
    }.get(category, "Other")


def _period_bounds(period: str, report_date: str | None) -> tuple[str, str, str]:
    tz = ZoneInfo(os.environ.get("AI_RESEARCH_TZ", "Asia/Tokyo"))
    target = date.fromisoformat(report_date) if report_date else datetime.now(tz).date()
    if period == "daily":
        start_local = datetime.combine(target, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        label = target.isoformat()
    elif period == "weekly":
        start_date = target - timedelta(days=target.weekday())
        start_local = datetime.combine(start_date, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=7)
        label = start_date.isoformat()
    else:
        start_date = target.replace(day=1)
        if start_date.month == 12:
            next_month = start_date.replace(year=start_date.year + 1, month=1)
        else:
            next_month = start_date.replace(month=start_date.month + 1)
        start_local = datetime.combine(start_date, time.min, tzinfo=tz)
        end_local = datetime.combine(next_month, time.min, tzinfo=tz)
        label = start_date.isoformat()[:7]

    return (
        start_local.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        end_local.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        label,
    )
