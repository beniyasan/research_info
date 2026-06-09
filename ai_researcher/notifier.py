from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .discord_bot import (
    DISCORD_BOT_TOKEN_ENV,
    DISCORD_CHANNEL_ID_ENV,
    make_comment_custom_id,
    make_rate_custom_id,
)


DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_BOT_MAX_RETRIES = 3
DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS = 30.0


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
    report_key: str | None = None,
) -> dict[str, Any]:
    discord_config = config.get("notifications", {}).get("discord", {})
    if not discord_config.get("enabled", True):
        return {"status": "skipped", "reason": "disabled"}

    bot_token = os.environ.get(DISCORD_BOT_TOKEN_ENV)
    channel_id = os.environ.get(DISCORD_CHANNEL_ID_ENV)
    if bot_token and channel_id:
        bot_result = _notify_report_bot(
            rows,
            period=period,
            label=label,
            report_path=report_path,
            candidates=candidates,
            selected=selected,
            newly_adopted=newly_adopted,
            config=config,
            drive=drive,
            verification=verification,
            report_key=report_key or f"{period}:{label}",
            bot_token=bot_token,
            channel_id=channel_id,
        )
        if bot_result.get("status") in {"sent", "partial"}:
            return bot_result

        webhook_result = _notify_report_webhook(
            rows,
            period=period,
            label=label,
            report_path=report_path,
            candidates=candidates,
            selected=selected,
            newly_adopted=newly_adopted,
            config=config,
            drive=drive,
            verification=verification,
        )
        bot_result["fallback"] = webhook_result
        return bot_result

    return _notify_report_webhook(
        rows,
        period=period,
        label=label,
        report_path=report_path,
        candidates=candidates,
        selected=selected,
        newly_adopted=newly_adopted,
        config=config,
        drive=drive,
        verification=verification,
    )


def _notify_report_webhook(
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


def _notify_report_bot(
    rows,
    *,
    period: str,
    label: str,
    report_path: str | Path,
    candidates: int,
    selected: int,
    newly_adopted: int,
    config: dict[str, Any],
    report_key: str,
    bot_token: str,
    channel_id: str,
    drive: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    discord_config = config.get("notifications", {}).get("discord", {})
    color = int(discord_config.get("color", 3447003))
    article_limit = int(discord_config.get("interactive_articles", len(rows)))
    sent_messages: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    summary = _build_summary_payload(
        period=period,
        label=label,
        report_path=report_path,
        candidates=candidates,
        selected=selected,
        newly_adopted=newly_adopted,
        color=color,
        drive=drive,
        verification=verification,
    )
    summary_result = post_discord_bot_message(bot_token, channel_id, summary)
    if summary_result.get("status") == "sent":
        sent_messages.append({"kind": "summary", **summary_result})
    else:
        errors.append({"kind": "summary", **summary_result})

    for index, row in enumerate(list(rows)[:article_limit], start=1):
        try:
            payload = _build_article_payload(row, report_key=report_key, rank=index, color=color)
            result = post_discord_bot_message(bot_token, channel_id, payload)
        except Exception as exc:
            errors.append({"kind": "article", "article_id": _row_get(row, "id"), "status": "failed", "error": str(exc)})
            continue
        if result.get("status") == "sent":
            sent_messages.append({"kind": "article", "article_id": _row_get(row, "id"), **result})
        else:
            errors.append({"kind": "article", "article_id": _row_get(row, "id"), **result})

    status = "sent" if not errors else "partial" if sent_messages else "failed"
    return {
        "status": status,
        "transport": "discord_bot",
        "sent": len(sent_messages),
        "errors": errors[:5],
        "report_key": report_key,
    }


def notify_test(config: dict[str, Any]) -> dict[str, Any]:
    discord_config = config.get("notifications", {}).get("discord", {})
    bot_token = os.environ.get(DISCORD_BOT_TOKEN_ENV)
    channel_id = os.environ.get(DISCORD_CHANNEL_ID_ENV)
    if bot_token and channel_id:
        return post_discord_bot_message(
            bot_token,
            channel_id,
            {
                "content": "AI Researcher Discord bot notification test.",
                "allowed_mentions": {"parse": []},
            },
        )

    webhook_url = os.environ.get(DISCORD_WEBHOOK_ENV)
    if not webhook_url:
        return {"status": "skipped", "reason": f"{DISCORD_WEBHOOK_ENV} not set"}

    payload = {
        "username": discord_config.get("username") or "AI Researcher",
        "allowed_mentions": {"parse": []},
        "content": "AI Researcher Discord notification test.",
    }
    return post_discord_webhook(webhook_url, payload)


def post_discord_bot_message(
    bot_token: str,
    channel_id: str,
    payload: dict[str, Any],
    *,
    max_retries: int = DISCORD_BOT_MAX_RETRIES,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(max_retries + 1):
        req = Request(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            data=body,
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "ai-researcher/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=20) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(response_body) if response_body else {}
                return {"status": "sent", "http_status": resp.status, "message_id": data.get("id"), "retries": attempt}
        except HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < max_retries:
                sleep_fn(_discord_retry_after(exc, response_body))
                continue
            result = {"status": "failed", "http_status": exc.code, "error": response_body[:500]}
            if exc.code == 429:
                result["retries"] = attempt
            return result
        except URLError as exc:
            return {"status": "failed", "error": str(exc)[:500], "retries": attempt}
    return {"status": "failed", "error": "exhausted Discord retry attempts", "retries": max_retries}


def _discord_retry_after(exc: HTTPError, response_body: str) -> float:
    for header_name in ("Retry-After", "X-RateLimit-Reset-After"):
        value = exc.headers.get(header_name) if exc.headers else None
        seconds = _coerce_retry_after(value)
        if seconds is not None:
            return min(seconds, DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS)
    try:
        payload = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError:
        payload = {}
    seconds = _coerce_retry_after(payload.get("retry_after"))
    if seconds is None:
        return 1.0
    return min(seconds, DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS)


def _coerce_retry_after(value: Any) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return max(seconds, 0.0)


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
    except URLError as exc:
        return {"status": "failed", "error": str(exc)[:500]}


def _build_summary_payload(
    *,
    period: str,
    label: str,
    report_path: str | Path,
    candidates: int,
    selected: int,
    newly_adopted: int,
    color: int,
    drive: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = (
        f"Candidates: {candidates} | Selected: {selected} | Newly adopted: {newly_adopted}\n"
        f"Report: `{report_path}`"
    )
    if drive and drive.get("web_view_link"):
        description += f"\nDrive: [Open report]({drive['web_view_link']})"
    if verification and verification.get("status") not in {None, "skipped"}:
        counts = verification.get("counts") or {}
        description += (
            f"\nVerification: {verification.get('status')} "
            f"({verification.get('checked', 0)} checked, {counts.get('verified', 0)} verified, "
            f"{counts.get('needs_review', 0)} review)"
        )
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{period.capitalize()} AI Research Report: {label}",
                "description": description,
                "color": color,
            }
        ],
    }


def _build_article_payload(row: Any, *, report_key: str, rank: int, color: int) -> dict[str, Any]:
    article_id = str(_row_get(row, "id"))
    display_title = _row_get(row, "translated_title") or _row_get(row, "title")
    summary = _row_get(row, "translated_summary") or _row_get(row, "summary") or ""
    score = float(_row_get(row, "preference_score", _row_get(row, "score", 0)) or 0)
    preference_adjustment = float(_row_get(row, "preference_adjustment", 0) or 0)
    description = _truncate(str(summary), 700) if summary else "No summary."
    fields = [
        {
            "name": "Source",
            "value": f"{_row_get(row, 'source_name', '-') } | score {score:.2f}",
            "inline": False,
        }
    ]
    if preference_adjustment:
        fields.append(
            {
                "name": "Preference signal",
                "value": f"{preference_adjustment:+.2f}",
                "inline": True,
            }
        )
    if _row_get(row, "selection_reason"):
        fields.append(
            {
                "name": "Selection reason",
                "value": _truncate(str(_row_get(row, "selection_reason")), 300),
                "inline": False,
            }
        )

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": _truncate(f"{rank}. {display_title}", 250),
                "url": _row_get(row, "url"),
                "description": description,
                "color": color,
                "fields": fields[:5],
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": make_rate_custom_id(report_key, article_id),
                        "placeholder": "評価を選択",
                        "min_values": 1,
                        "max_values": 1,
                        "options": [
                            {"label": "刺さる", "value": "刺さる", "description": "強く響いた、深掘りしたい"},
                            {"label": "追う", "value": "追う", "description": "継続観測したい"},
                            {"label": "既知", "value": "既知", "description": "重要だが既に把握済み"},
                            {"label": "弱い", "value": "弱い", "description": "優先度は低い"},
                            {"label": "方向違い", "value": "方向違い", "description": "関心の方向から外れている"},
                        ],
                    }
                ],
            },
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 2,
                        "custom_id": make_comment_custom_id(report_key, article_id),
                        "label": "コメント",
                    },
                    {
                        "type": 2,
                        "style": 5,
                        "url": _row_get(row, "url"),
                        "label": "記事を開く",
                    },
                ],
            },
        ],
    }


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _truncate(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
