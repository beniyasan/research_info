from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .utils import normalize_space, parse_date


DEFAULT_AI_TERMS = [
    "ai",
    "artificial intelligence",
    "生成ai",
    "生成 ai",
    "llm",
    "large language model",
    "agent",
    "agents",
    "ai agent",
    "rag",
    "mcp",
    "model context protocol",
    "openai",
    "chatgpt",
    "claude",
    "anthropic",
    "gemini",
    "deepmind",
    "hugging face",
    "llama",
    "mistral",
    "cursor",
    "claude code",
    "copilot",
    "swe-agent",
    "eval",
    "evals",
    "inference",
    "fine-tuning",
    "embedding",
    "vector database",
    "ai駆動",
    "aiエージェント",
]


STOP_CANDIDATES = {
    "api",
    "apps",
    "abstract",
    "announce type",
    "authors",
    "blog",
    "code",
    "data",
    "demo",
    "docs",
    "journal-ref",
    "github",
    "guide",
    "image",
    "learn",
    "model",
    "news",
    "open",
    "post",
    "release",
    "research",
    "tool",
    "tools",
    "update",
    "user",
    "video",
    "web",
    "zenn",
    "qiita",
}


def score_article(article: dict[str, Any], source: dict[str, Any], keywords: list[str]) -> dict[str, Any]:
    title = normalize_space(article.get("title", ""))
    summary = normalize_space(article.get("summary", ""))
    tags = [str(tag) for tag in article.get("tags", [])]
    hay_title = title.lower()
    hay_body = f"{title}\n{summary}\n{' '.join(tags)}".lower()
    terms = _dedupe_terms([*DEFAULT_AI_TERMS, *keywords])

    score = 0.0
    reasons: list[str] = []
    matched: list[str] = []

    for term in terms:
        term_l = term.lower()
        if not term_l:
            continue
        if term_l in hay_title:
            score += 2.0 if len(term_l) > 2 else 1.0
            matched.append(term)
        elif term_l in hay_body:
            score += 0.9 if len(term_l) > 2 else 0.4
            matched.append(term)

    matched = _dedupe_terms(matched)
    if matched:
        reasons.append("matched: " + ", ".join(matched[:8]))

    category = source.get("category", "")
    if category in {"official", "research"}:
        score += 0.6
        reasons.append(f"trusted category: {category}")
    elif category in {"jp-tech", "dev-community", "newsletter"}:
        score += 0.3
    elif category == "slides":
        score += 0.15

    priority = float(source.get("priority") or 1.0)
    score += min(priority, 2.0) * 0.25

    recency = _recency_bonus(article.get("published_at"))
    score += recency
    if recency:
        reasons.append("recent")

    raw = article.get("raw") or {}
    popularity = 0.0
    if "likes_count" in raw:
        popularity += min(float(raw.get("likes_count") or 0), 100.0) / 100.0
    if "stocks_count" in raw:
        popularity += min(float(raw.get("stocks_count") or 0), 100.0) / 120.0
    if "points" in raw:
        popularity += min(float(raw.get("points") or 0), 500.0) / 250.0
    if "num_comments" in raw:
        popularity += min(float(raw.get("num_comments") or 0), 300.0) / 300.0
    if popularity:
        score += min(popularity, 2.0)
        reasons.append("popular")

    return {
        "score": round(score, 3),
        "relevance": 1 if score >= 2.5 else 0,
        "reasons": reasons,
        "matched_keywords": matched[:20],
    }


def extract_candidate_keywords(article: dict[str, Any]) -> list[str]:
    text = f"{article.get('title', '')} {article.get('summary', '')} {' '.join(article.get('tags', []))}"
    candidates: set[str] = set()

    for tag in article.get("tags", []):
        tag_text = str(tag).strip()
        if _is_candidate_term(tag_text):
            candidates.add(tag_text)

    # Product and framework names tend to be CamelCase, ALLCAPS, or contain digits/hyphens.
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9]*(?:[- ][A-Z]?[A-Za-z0-9]+){0,2}\b", text):
        term = match.group(0).strip()
        if _is_candidate_term(term):
            candidates.add(term)

    for match in re.finditer(r"\b[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+){1,3}\b", text):
        term = match.group(0).strip()
        if _is_candidate_term(term):
            candidates.add(term)

    return sorted(candidates, key=lambda t: (len(t), t.lower()))[:20]


def _recency_bonus(value: str | None) -> float:
    parsed = parse_date(value)
    if not parsed:
        return 0.2
    dt = datetime.fromisoformat(parsed)
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    if age_days <= 1:
        return 1.2
    if age_days <= 7:
        return 0.8
    if age_days <= 30:
        return 0.3
    return 0.0


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for term in terms:
        cleaned = normalize_space(str(term))
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _is_candidate_term(term: str) -> bool:
    cleaned = normalize_space(term)
    key = cleaned.lower()
    if len(cleaned) < 3 or len(cleaned) > 48:
        return False
    if key in STOP_CANDIDATES:
        return False
    if re.fullmatch(r"\d+", cleaned):
        return False
    if cleaned.count(" ") > 3:
        return False
    if re.fullmatch(r"cs\.[a-z]{2}", key):
        return False
    if key in {"cl cs", "cs ai", "cs cl"}:
        return False
    return True
