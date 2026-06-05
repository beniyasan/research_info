from __future__ import annotations

import json
from collections import Counter
from typing import Any

from . import db
from .gemini_grounding import run_grounded_gemini
from .utils import canonical_url


VALID_STATUSES = {
    "verified",
    "primary_source_found",
    "possibly_duplicate",
    "stale_or_unclear",
    "needs_review",
}


def verify_selected_articles(
    conn,
    config: dict[str, Any],
    selected_articles: list[dict[str, Any]],
    *,
    report_key: str,
    period: str,
    label: str,
) -> dict[str, Any]:
    verification_config = config.get("grounding", {}).get("article_verification", {})
    if not config.get("grounding", {}).get("enabled", True) or not verification_config.get("enabled", True):
        return {"status": "skipped", "reason": "disabled", "items": []}
    if not selected_articles:
        return {"status": "skipped", "reason": "no selected articles", "items": []}

    existing = db.get_article_verifications(conn, report_key)
    pending = [article for article in selected_articles if article["id"] not in existing]
    batch_size = max(1, int(verification_config.get("batch_size", 8)))
    timeout = int(verification_config.get("timeout_seconds") or config.get("grounding", {}).get("timeout_seconds", 180))
    max_output_tokens = int(verification_config.get("max_output_tokens", 4096))
    checked = 0
    failures = []
    processed_ids: set[str] = set()

    try:
        for chunk in _chunks(pending, batch_size):
            result = run_grounded_gemini(
                _verification_prompt(chunk, period=period, label=label),
                config,
                timeout=timeout,
                max_output_tokens=max_output_tokens,
                temperature=float(verification_config.get("temperature", config.get("grounding", {}).get("temperature", 0.1))),
            )
            data = _loads_json_object(result["text"])
            by_id = {article["id"]: article for article in chunk}
            seen_ids: set[str] = set()

            for item in data.get("articles", []):
                article_id = str(item.get("id") or "").strip()
                if article_id not in by_id or article_id in seen_ids:
                    continue
                seen_ids.add(article_id)
                processed_ids.add(article_id)
                _save_verification(conn, report_key, article_id, item, result)
                checked += 1

            for article_id in set(by_id) - seen_ids:
                processed_ids.add(article_id)
                db.upsert_article_verification(
                    conn,
                    report_key=report_key,
                    article_id=article_id,
                    status="needs_review",
                    verification_summary="Gemini Search の検証結果にこの記事 ID が含まれませんでした。",
                    grounding_queries=result["grounding"]["queries"],
                    grounding_sources=result["grounding"]["sources"],
                    metadata={"reason": "missing_from_grounded_response"},
                )
                checked += 1
    except Exception as exc:
        if verification_config.get("fail_report_on_error", False):
            raise
        failures.append(str(exc))
        for article in pending:
            if article["id"] in processed_ids:
                continue
            db.upsert_article_verification(
                conn,
                report_key=report_key,
                article_id=article["id"],
                status="needs_review",
                verification_summary=f"Gemini Search 検証に失敗しました: {str(exc)[:240]}",
                metadata={"error": str(exc)[:800]},
            )

    items = db.get_article_verifications(conn, report_key)
    counts = Counter(item["status"] for item in items.values())
    status = "ok"
    if failures:
        status = "failed" if not checked else "partial"
    return {
        "status": status,
        "checked": len(items),
        "newly_checked": checked,
        "cached": len(existing),
        "counts": dict(counts),
        "failures": failures[:3],
        "items": list(items.values()),
    }


def _save_verification(
    conn,
    report_key: str,
    article_id: str,
    item: dict[str, Any],
    result: dict[str, Any],
) -> None:
    status = str(item.get("status") or "needs_review").strip()
    if status not in VALID_STATUSES:
        status = "needs_review"
    primary_url = canonical_url(item.get("primary_source_url") or "")
    db.upsert_article_verification(
        conn,
        report_key=report_key,
        article_id=article_id,
        status=status,
        primary_source_url=primary_url or None,
        primary_source_title=str(item.get("primary_source_title") or "").strip() or None,
        verification_summary=str(item.get("summary") or "").strip() or None,
        confidence=_coerce_confidence(item.get("confidence")),
        grounding_queries=result["grounding"]["queries"],
        grounding_sources=result["grounding"]["sources"],
        metadata={
            "model": result["model"],
            "location": result["location"],
            "usage": result.get("usage") or {},
            "response_mime_type_fallback": result.get("response_mime_type_fallback", False),
        },
    )


def _verification_prompt(articles: list[dict[str, Any]], *, period: str, label: str) -> str:
    compact_articles = []
    for article in articles:
        compact_articles.append(
            {
                "id": article["id"],
                "title": article["title"],
                "url": article["url"],
                "source": article.get("source_name"),
                "published_at": article.get("published_at"),
                "summary": _truncate(article.get("summary") or "", 600),
                "selected_reason": _truncate(article.get("selection_reason") or "", 240),
            }
        )

    return f"""
You are verifying selected articles for a Japanese AI engineering research report.
Use Google Search grounding to verify whether each selected article appears real, current, and attributable.

Report: {period} {label}

Status definitions:
- verified: the given article URL/title/source are consistent enough to keep as-is.
- primary_source_found: the selected article is secondary coverage, and you found a clearer official or primary source.
- possibly_duplicate: the article appears to duplicate another item or syndicated copy.
- stale_or_unclear: the item looks old, unavailable, or not clearly current for this report period.
- needs_review: you cannot verify enough information.

Rules:
- Return one result for every input id.
- Do not invent facts. If uncertain, use needs_review.
- Write summary in Japanese.
- primary_source_url may be empty when the selected article itself is the right source.
- Return JSON only. Do not include Markdown fences.

Schema:
{{
  "articles": [
    {{
      "id": "input article id",
      "status": "verified | primary_source_found | possibly_duplicate | stale_or_unclear | needs_review",
      "primary_source_url": "official or primary source URL if found, otherwise empty",
      "primary_source_title": "primary source title if found, otherwise empty",
      "summary": "Japanese verification summary",
      "confidence": 0.0
    }}
  ]
}}

Selected articles:
{json.dumps(compact_articles, ensure_ascii=False, indent=2)}
""".strip()


def _loads_json_object(output: str) -> dict[str, Any]:
    text = output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in Gemini grounding output")
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Gemini grounding output must be a JSON object")
    return data


def _chunks(values: list[dict[str, Any]], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))


def _truncate(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
