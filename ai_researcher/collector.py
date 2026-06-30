from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import db
from .config import score_threshold
from .fetchers import fetch_source
from .scorer import extract_candidate_keywords, score_article
from .utils import canonical_url, cluster_key, local_today, stable_id, url_domain, utc_now


def collect(conn, config: dict[str, Any], *, raw_dir: str | Path = "data/raw") -> dict[str, Any]:
    threshold = score_threshold(config)
    keywords = db.get_keywords(conn)
    sources = db.get_sources(conn)
    run_id = db.begin_run(conn, "collect", {"source_count": len(sources)})

    fetched_articles: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    source_summaries: dict[str, dict[str, int]] = {}
    candidate_terms: Counter[str] = Counter()

    try:
        for row in sources:
            source = dict(row)
            try:
                items = fetch_source(source)
            except Exception as exc:
                errors.append({"source_id": source["id"], "error": str(exc)})
                continue

            relevant_hits = 0
            for item in items:
                url = canonical_url(item.get("url"))
                if not url:
                    continue
                scored = score_article(item, source, keywords, threshold=threshold)
                article = {
                    **item,
                    **scored,
                    "id": stable_id(url),
                    "source_id": source["id"],
                    "url": url,
                    "fetched_at": utc_now(),
                    "cluster_key": cluster_key(item.get("title", ""), url),
                }
                db.upsert_article(conn, article)
                fetched_articles.append(article)

                if article["relevance"]:
                    relevant_hits += 1
                    domain = url_domain(url)
                    if domain:
                        db.record_candidate(conn, "domain", domain, article["id"], metadata={"source_id": source["id"]})
                    if article.get("author"):
                        db.record_candidate(conn, "author", article["author"], article["id"], metadata={"source_id": source["id"]})

                    terms = extract_candidate_keywords(article)
                    candidate_terms.update(terms)
                    for term in terms:
                        db.record_candidate(conn, "keyword", term, article["id"], metadata={"source_id": source["id"]})

            db.update_source_hits(conn, source["id"], relevant_hits)
            source_summaries[source["id"]] = {
                "fetched": len(items),
                "relevant": relevant_hits,
            }

        db.record_keyword_hits(conn, [term for term, count in candidate_terms.items() if count >= 2])
        conn.commit()

        raw_path = _write_raw(raw_dir, fetched_articles, errors)
        details = {
            "fetched": len(fetched_articles),
            "errors": len(errors),
            "raw_path": str(raw_path),
            "sources": source_summaries,
        }
        db.finish_run(conn, run_id, "ok" if not errors else "partial", details)
        return details
    except Exception as exc:
        conn.rollback()
        db.finish_run(conn, run_id, "failed", {"error": str(exc)})
        raise


def _write_raw(raw_dir: str | Path, articles: list[dict[str, Any]], errors: list[dict[str, str]]) -> Path:
    date = local_today()
    path = Path(raw_dir) / date / "articles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"articles": articles, "errors": errors}, fh, ensure_ascii=False, indent=2)
    return path
