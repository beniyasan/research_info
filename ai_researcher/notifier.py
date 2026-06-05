from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"


def notify_report(
    rows,
    *,
    period: str,
    label: str,
    report_path: str | Path,
    candidates: int,
    selected: int,
    newly_adopted: int,
    config: dict[str, Any],
    drive: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    discord_config = config.get("notifications", {}).get("discord", {})
    if not discord_config.get("enabled", True):
        return {"status": "skipped", "reason": "disabled"}

    webhook_url = os.environ.get(DISCORD_WEBHOOK_ENV)
    if not webhook_url:
        return {"status": "skipped", "reason": f"{DISCORD_WEBHOOK_ENV} not set"}

    top_count = int(discord_config.get("top_articles", 5))
    username = discord_config.get("username") or "AI Researcher"
    title = f"{period.capitalize()} AI Research Report: {label}"
    description = (
        f"Candidates: {candidates} | Selected: {selected} | "
        f"Newly adopted: {newly_adopted}\n"
        f"Report: `{report_path}`"
    )
    if drive and drive.get("web_view_link"):
        description += f"\nDrive: [Open report]({drive['web_view_link']})"
    if verification and verification.get("status") not in {None, "skipped"}:
        counts = verification.get("counts") or {}
        verified = counts.get("verified", 0)
        needs_review = counts.get("needs_review", 0)
        description += (
            f"\nVerification: {verification.get('status')} "
            f"({verification.get('checked', 0)} checked, {verified} verified, {needs_review} review)"
        )

    fields = []
    for index, row in enumerate(rows[:top_count], start=1):
        display_title = row.get("translated_title") or row["title"]
        fields.append(
            {
                "name": f"{index}. {_truncate(display_title, 180)}",
                "value": f"[Open article]({row['url']})\n{row['source_name']} | score {row['score']:.2f}",
                "inline": False,
            }
        )

    if not fields:
        fields.append({"name": "No relevant articles", "value": "No articles matched the report threshold.", "inline": False})

    payload = {
        "username": username,
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": int(discord_config.get("color", 3447003)),
                "fields": fields[:10],
            }
        ],
    }
    return post_discord_webhook(webhook_url, payload)


def notify_test(config: dict[str, Any]) -> dict[str, Any]:
    discord_config = config.get("notifications", {}).get("discord", {})
    webhook_url = os.environ.get(DISCORD_WEBHOOK_ENV)
    if not webhook_url:
        return {"status": "skipped", "reason": f"{DISCORD_WEBHOOK_ENV} not set"}

    payload = {
        "username": discord_config.get("username") or "AI Researcher",
        "allowed_mentions": {"parse": []},
        "content": "AI Researcher Discord notification test.",
    }
    return post_discord_webhook(webhook_url, payload)


def post_discord_webhook(webhook_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ai-researcher/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as resp:
            return {"status": "sent", "http_status": resp.status}
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return {"status": "failed", "http_status": exc.code, "error": response_body[:500]}


def _truncate(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
