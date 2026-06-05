from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from . import db
from .fetchers import fetch_rss
from .gemini_grounding import run_grounded_gemini
from .utils import canonical_url, stable_id, url_domain


SOURCE_CATEGORIES = {
    "official",
    "research",
    "dev-community",
    "newsletter",
    "slides",
    "news",
    "general",
}


def discover_sources(
    conn,
    config: dict[str, Any],
    *,
    cadence: str = "weekly",
    limit: int | None = None,
) -> dict[str, Any]:
    grounding_config = config.get("grounding", {})
    discovery_config = grounding_config.get("source_discovery", {})
    if not grounding_config.get("enabled", True) or not discovery_config.get("enabled", True):
        return {"status": "skipped", "reason": "disabled"}

    run_id = db.begin_run(conn, f"source_discovery:{cadence}")
    try:
        existing_sources = _existing_sources(conn)
        max_sources = int(limit or discovery_config.get("limit", 10))
        result = run_grounded_gemini(
            _discovery_prompt(
                config,
                existing_sources=existing_sources,
                cadence=cadence,
                limit=max_sources,
                queries=list(discovery_config.get("queries") or []),
            ),
            config,
            timeout=int(discovery_config.get("timeout_seconds") or grounding_config.get("timeout_seconds", 180)),
            max_output_tokens=int(discovery_config.get("max_output_tokens", 4096)),
            temperature=float(discovery_config.get("temperature", grounding_config.get("temperature", 0.1))),
        )
        data = _loads_json_object(result["text"])
        items = _normalize_items(data.get("sources", []), max_sources=max_sources)
        details = _store_discoveries(conn, config, discovery_config, items, result, cadence)
        conn.commit()
        db.finish_run(conn, run_id, "ok", details)
        return details
    except Exception as exc:
        conn.rollback()
        db.finish_run(conn, run_id, "failed", {"error": str(exc)})
        if discovery_config.get("fail_run_on_error", False):
            raise
        return {"status": "failed", "reason": str(exc)[:800], "cadence": cadence}


def _store_discoveries(
    conn,
    config: dict[str, Any],
    discovery_config: dict[str, Any],
    items: list[dict[str, Any]],
    result: dict[str, Any],
    cadence: str,
) -> dict[str, Any]:
    min_confidence = float(discovery_config.get("min_confidence", 0.65))
    validate_feeds = bool(discovery_config.get("validate_feeds", True))
    candidate_source_limit = int(discovery_config.get("candidate_source_limit", 5))
    added_sources = 0
    statuses: Counter[str] = Counter()
    discoveries = []

    for item in items:
        status = "stored"
        source_id = None
        url = canonical_url(item.get("url") or "")
        feed_url = canonical_url(item.get("suggested_feed_url") or "")
        domain = item.get("domain") or url_domain(feed_url or url)
        confidence = float(item.get("confidence") or 0.0)
        metadata = {
            "cadence": cadence,
            "keywords": item.get("keywords") or [],
            "model": result["model"],
            "location": result["location"],
            "usage": result.get("usage") or {},
            "response_mime_type_fallback": result.get("response_mime_type_fallback", False),
        }

        if not url and not feed_url:
            status = "invalid"
        elif confidence < min_confidence:
            status = "low_confidence"
        elif feed_url and added_sources < candidate_source_limit:
            existing = db.find_source_by_url(conn, feed_url)
            if existing:
                status = "existing_source"
                source_id = existing["id"]
            elif validate_feeds and not _feed_is_valid(feed_url):
                status = "feed_invalid"
            else:
                source_id = _insert_candidate_source(conn, item, feed_url, domain)
                status = "candidate_source"
                added_sources += 1
        elif domain:
            db.record_candidate(
                conn,
                "domain",
                domain,
                None,
                metadata={"source": "gemini_grounding", "title": item.get("title"), "url": url, "cadence": cadence},
            )
            status = "candidate_domain"

        discovery_id = stable_id(cadence, url, feed_url, item.get("title") or "")[:32]
        db.upsert_source_discovery(
            conn,
            discovery_id=discovery_id,
            search_query="; ".join(discovery_config.get("queries") or []),
            title=item.get("title") or domain or "(untitled)",
            url=url or feed_url,
            domain=domain,
            suggested_feed_url=feed_url or None,
            source_type=item.get("source_type"),
            category=item.get("category"),
            reason=item.get("reason"),
            confidence=confidence,
            status=status,
            source_id=source_id,
            grounding_queries=result["grounding"]["queries"],
            grounding_sources=result["grounding"]["sources"],
            metadata=metadata,
        )
        statuses[status] += 1
        discoveries.append(
            {
                "title": item.get("title"),
                "url": url or feed_url,
                "feed_url": feed_url or None,
                "domain": domain,
                "status": status,
                "source_id": source_id,
                "confidence": confidence,
            }
        )

    return {
        "status": "ok",
        "cadence": cadence,
        "discovered": len(items),
        "candidate_sources": added_sources,
        "counts": dict(statuses),
        "grounding_queries": result["grounding"]["queries"],
        "discoveries": discoveries,
    }


def _insert_candidate_source(conn, item: dict[str, Any], feed_url: str, domain: str) -> str:
    base_id = _source_id(domain, item.get("title") or domain, feed_url)
    source_id = base_id
    suffix = 2
    while db.source_id_exists(conn, source_id):
        source_id = f"{base_id}-{suffix}"
        suffix += 1

    category = item.get("category") or "general"
    if category not in SOURCE_CATEGORIES:
        category = "general"
    db.seed_source(
        conn,
        {
            "id": source_id,
            "name": item.get("title") or domain,
            "type": "rss",
            "url": feed_url,
            "category": category,
            "status": "candidate",
            "priority": 0.8,
            "max_items": 20,
            "notes": f"Discovered by Gemini Search. {item.get('reason') or ''}".strip(),
        },
    )
    return source_id


def _discovery_prompt(
    config: dict[str, Any],
    *,
    existing_sources: list[dict[str, Any]],
    cadence: str,
    limit: int,
    queries: list[str],
) -> str:
    keywords = list(config.get("keywords") or [])[:80]
    return f"""
You are improving an AI engineering research collector.
Use Google Search grounding to discover high-signal public sources that are not already in the collector.

Cadence: {cadence}
Return at most {limit} sources.

Prefer:
- official AI/vendor/product/research blogs with RSS or Atom feeds
- engineering blogs covering LLM apps, agents, RAG, evaluations, inference, developer tools
- durable newsletters or research digests with RSS/Atom
- useful slide/deck sources only when they have a stable public feed

Avoid:
- duplicates of existing sources or domains unless the feed is clearly distinct and high value
- one-off articles, SEO listicles, social-media-only accounts, and private/login-only sources
- sources that are mostly business press with low technical value

Known keywords:
{json.dumps(keywords, ensure_ascii=False)}

Existing sources:
{json.dumps(existing_sources, ensure_ascii=False, indent=2)}

Search themes:
{json.dumps(queries, ensure_ascii=False, indent=2)}

Return JSON only. Do not include Markdown fences.
Schema:
{{
  "sources": [
    {{
      "title": "source name",
      "url": "source homepage or index URL",
      "domain": "example.com",
      "suggested_feed_url": "RSS or Atom URL if found, otherwise empty",
      "source_type": "rss | atom | html_index | newsletter | slides | domain",
      "category": "official | research | dev-community | newsletter | slides | news | general",
      "reason": "why this source could improve the final report, in Japanese",
      "confidence": 0.0,
      "keywords": ["AI topic terms associated with this source"]
    }}
  ]
}}
""".strip()


def _existing_sources(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, type, url, query, category, status
        FROM sources
        WHERE status IN ('active', 'candidate', 'low_priority')
        ORDER BY priority DESC, name
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "url": row["url"],
            "query": row["query"],
            "category": row["category"],
            "status": row["status"],
            "domain": url_domain(row["url"]),
        }
        for row in rows
    ]


def _normalize_items(values: Any, *, max_sources: int) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    items = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        url = canonical_url(value.get("suggested_feed_url") or value.get("url") or "")
        key = url or str(value.get("domain") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        category = str(value.get("category") or "general").strip()
        source_type = str(value.get("source_type") or "domain").strip()
        items.append(
            {
                "title": str(value.get("title") or "").strip(),
                "url": canonical_url(value.get("url") or ""),
                "domain": str(value.get("domain") or "").strip().lower(),
                "suggested_feed_url": canonical_url(value.get("suggested_feed_url") or ""),
                "source_type": source_type,
                "category": category if category in SOURCE_CATEGORIES else "general",
                "reason": str(value.get("reason") or "").strip(),
                "confidence": _coerce_confidence(value.get("confidence")),
                "keywords": [str(term).strip() for term in value.get("keywords") or [] if str(term).strip()][:12],
            }
        )
        if len(items) >= max_sources:
            break
    return items


def _feed_is_valid(feed_url: str) -> bool:
    try:
        return bool(fetch_rss(feed_url, max_items=3))
    except Exception:
        return False


def _source_id(domain: str, title: str, feed_url: str) -> str:
    domain_part = re.sub(r"[^a-z0-9]+", "-", (domain or "source").lower()).strip("-")
    title_part = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = "-".join(part for part in [domain_part, title_part] if part)[:42].strip("-")
    return f"disc-{slug}-{stable_id(feed_url)[:8]}"


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


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
