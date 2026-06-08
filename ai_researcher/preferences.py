from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from .utils import url_domain, utc_now


RATING_STICK = "刺さる"
RATING_FOLLOW = "追う"
RATING_KNOWN = "既知"
RATING_WEAK = "弱い"
RATING_WRONG = "方向違い"
RATING_COMMENT = "コメント"

RATING_WEIGHTS = {
    RATING_STICK: 2.0,
    RATING_FOLLOW: 1.2,
    RATING_KNOWN: -0.2,
    RATING_WEAK: -1.0,
    RATING_WRONG: -2.0,
    RATING_COMMENT: 0.3,
}

RATING_OPTIONS = tuple(RATING_WEIGHTS)
MIN_FEEDBACK_FOR_SELECTION = 20
MAX_PREFERENCE_ADJUSTMENT = 0.7


def record_feedback(
    conn,
    *,
    report_key: str,
    article_id: str,
    discord_user_id: str | int,
    rating: str | None = None,
    comment: str | None = None,
    message_id: str | int | None = None,
    channel_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if rating is None and comment is None:
        raise ValueError("rating or comment is required")
    if rating is not None:
        validate_rating(rating)

    now = utc_now()
    user_id = str(discord_user_id)
    message_id_text = str(message_id) if message_id is not None else None
    channel_id_text = str(channel_id) if channel_id is not None else None
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

    existing = conn.execute(
        """
        SELECT rating, comment, created_at
        FROM article_feedback
        WHERE report_key = ? AND article_id = ? AND discord_user_id = ?
        """,
        (report_key, article_id, user_id),
    ).fetchone()

    final_rating = rating or (existing["rating"] if existing else RATING_COMMENT)
    final_comment = existing["comment"] if existing else None
    event_type = "rating" if rating and rating != RATING_COMMENT else "comment"
    if comment:
        final_comment = _append_comment(final_comment, comment, now)

    conn.execute(
        """
        INSERT INTO article_feedback(
          report_key, article_id, discord_user_id, rating, comment, message_id,
          channel_id, created_at, updated_at, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_key, article_id, discord_user_id) DO UPDATE SET
          rating = excluded.rating,
          comment = excluded.comment,
          message_id = COALESCE(excluded.message_id, article_feedback.message_id),
          channel_id = COALESCE(excluded.channel_id, article_feedback.channel_id),
          updated_at = excluded.updated_at,
          metadata = excluded.metadata
        """,
        (
            report_key,
            article_id,
            user_id,
            final_rating,
            final_comment,
            message_id_text,
            channel_id_text,
            existing["created_at"] if existing else now,
            now,
            metadata_json,
        ),
    )
    conn.execute(
        """
        INSERT INTO article_feedback_events(
          report_key, article_id, discord_user_id, event_type, rating, comment,
          message_id, channel_id, event_at, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_key,
            article_id,
            user_id,
            event_type,
            rating or (RATING_COMMENT if comment else final_rating),
            comment,
            message_id_text,
            channel_id_text,
            now,
            metadata_json,
        ),
    )
    return {
        "report_key": report_key,
        "article_id": article_id,
        "discord_user_id": user_id,
        "rating": final_rating,
        "comment": final_comment,
        "updated_at": now,
    }


def validate_rating(rating: str) -> None:
    if rating not in RATING_WEIGHTS:
        raise ValueError(f"Unsupported rating: {rating}")


def feedback_summary(conn, *, min_feedback_count: int = MIN_FEEDBACK_FOR_SELECTION) -> dict[str, Any]:
    rows = _feedback_rows(conn)
    context = build_preference_context(conn, min_feedback_count=min_feedback_count, rows=rows)
    return {
        "total_feedback": context["total_feedback"],
        "active": context["active"],
        "min_feedback_count": context["min_feedback_count"],
        "rating_counts": context["rating_counts"],
        "top_positive_features": context["top_positive_features"],
        "top_negative_features": context["top_negative_features"],
        "recent_comments": context["recent_comments"],
    }


def apply_preference_adjustments(
    conn,
    candidates: list[dict[str, Any]],
    *,
    min_feedback_count: int = MIN_FEEDBACK_FOR_SELECTION,
    max_adjustment: float = MAX_PREFERENCE_ADJUSTMENT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    context = build_preference_context(conn, min_feedback_count=min_feedback_count)
    adjusted: list[dict[str, Any]] = []
    for candidate in candidates:
        base_score = float(candidate.get("score") or 0)
        adjustment = (
            _preference_adjustment(candidate, context, max_adjustment=max_adjustment)
            if context["active"]
            else 0.0
        )
        adjusted.append(
            {
                **candidate,
                "base_score": round(base_score, 3),
                "preference_adjustment": round(adjustment, 3),
                "preference_score": round(base_score + adjustment, 3),
            }
        )
    adjusted.sort(
        key=lambda row: (
            float(row.get("preference_score") or 0),
            float(row.get("score") or 0),
            float(row.get("source_priority") or 0),
        ),
        reverse=True,
    )
    return adjusted, context


def build_preference_context(
    conn,
    *,
    min_feedback_count: int = MIN_FEEDBACK_FOR_SELECTION,
    rows: list[Any] | None = None,
) -> dict[str, Any]:
    rows = rows if rows is not None else _feedback_rows(conn)
    rating_counts = Counter(str(row["rating"]) for row in rows)
    feature_stats = _build_feature_stats(rows)
    comments = [
        {
            "report_key": row["report_key"],
            "article_id": row["article_id"],
            "rating": row["rating"],
            "comment": row["comment"],
            "updated_at": row["updated_at"],
        }
        for row in rows
        if row["comment"]
    ][:8]
    return {
        "total_feedback": len(rows),
        "active": len(rows) >= min_feedback_count,
        "min_feedback_count": min_feedback_count,
        "rating_counts": dict(rating_counts),
        "feature_stats": feature_stats,
        "top_positive_features": _top_features(feature_stats, positive=True),
        "top_negative_features": _top_features(feature_stats, positive=False),
        "recent_comments": comments,
    }


def _feedback_rows(conn) -> list[Any]:
    return list(
        conn.execute(
            """
            SELECT
              f.report_key,
              f.article_id,
              f.discord_user_id,
              f.rating,
              f.comment,
              f.updated_at,
              a.source_id,
              a.url,
              a.title,
              a.matched_keywords,
              a.tags,
              s.name AS source_name,
              s.category AS source_category
            FROM article_feedback f
            JOIN articles a ON a.id = f.article_id
            LEFT JOIN sources s ON s.id = a.source_id
            ORDER BY f.updated_at DESC
            """
        )
    )


def _build_feature_stats(rows: list[Any]) -> dict[str, dict[str, float | int]]:
    stats: dict[str, dict[str, float | int]] = defaultdict(lambda: {"total": 0.0, "count": 0, "average": 0.0})
    for row in rows:
        rating = str(row["rating"])
        weight = RATING_WEIGHTS.get(rating, 0.0)
        if row["comment"]:
            weight += RATING_WEIGHTS[RATING_COMMENT]
        for feature in _article_features(row):
            stats[feature]["total"] = float(stats[feature]["total"]) + weight
            stats[feature]["count"] = int(stats[feature]["count"]) + 1

    normalized: dict[str, dict[str, float | int]] = {}
    for feature, values in stats.items():
        count = int(values["count"])
        if count <= 0:
            continue
        total = float(values["total"])
        normalized[feature] = {
            "total": round(total, 3),
            "count": count,
            "average": round(total / count, 3),
        }
    return normalized


def _preference_adjustment(candidate: dict[str, Any], context: dict[str, Any], *, max_adjustment: float) -> float:
    feature_stats: dict[str, dict[str, float | int]] = context.get("feature_stats") or {}
    total = 0.0
    for feature in _article_features(candidate):
        values = feature_stats.get(feature)
        if not values:
            continue
        total += float(values["average"]) * _feature_weight(feature)
    return max(-max_adjustment, min(max_adjustment, total))


def _article_features(row: Any) -> list[str]:
    get = row.get if isinstance(row, dict) else lambda key, default=None: row[key] if key in row.keys() else default
    features: list[str] = []
    source_id = get("source_id")
    if source_id:
        features.append(f"source:{source_id}")
    category = get("source_category")
    if category:
        features.append(f"category:{category}")
    domain = url_domain(get("url"))
    if domain:
        features.append(f"domain:{domain}")
    for term in _loads_json_list(get("matched_keywords")):
        cleaned = str(term).strip().lower()
        if cleaned:
            features.append(f"keyword:{cleaned}")
    for tag in _loads_json_list(get("tags")):
        cleaned = str(tag).strip().lower()
        if cleaned:
            features.append(f"tag:{cleaned}")
    return list(dict.fromkeys(features))


def _loads_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _feature_weight(feature: str) -> float:
    if feature.startswith("source:"):
        return 0.18
    if feature.startswith("category:"):
        return 0.12
    if feature.startswith("domain:"):
        return 0.10
    if feature.startswith("keyword:"):
        return 0.09
    if feature.startswith("tag:"):
        return 0.06
    return 0.05


def _top_features(feature_stats: dict[str, dict[str, float | int]], *, positive: bool, limit: int = 8) -> list[dict[str, Any]]:
    direction = 1 if positive else -1
    features = [
        {
            "feature": feature,
            "average": values["average"],
            "count": values["count"],
            "score": round(float(values["average"]) * int(values["count"]), 3),
        }
        for feature, values in feature_stats.items()
        if direction * float(values["average"]) > 0
    ]
    features.sort(key=lambda item: abs(float(item["score"])), reverse=True)
    return features[:limit]


def _append_comment(existing: str | None, comment: str, now: str) -> str:
    cleaned = " ".join(str(comment).split())
    entry = f"[{now}] {cleaned}"
    return f"{existing}\n\n{entry}" if existing else entry
