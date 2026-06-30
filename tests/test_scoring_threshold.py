from __future__ import annotations

from ai_researcher import db
from ai_researcher.collector import collect
from ai_researcher.scorer import score_article


def _threshold_article() -> dict[str, object]:
    return {
        "url": "https://example.com/agent-threshold",
        "title": "Agent",
        "summary": "",
        "author": "Example Author",
        "published_at": "2000-01-01T00:00:00+00:00",
        "tags": [],
        "raw": {},
    }


def _threshold_source() -> dict[str, object]:
    return {
        "id": "threshold-source",
        "name": "Threshold Source",
        "type": "rss",
        "url": "https://example.com/feed.xml",
        "category": "jp-tech",
        "priority": 1.8,
        "max_items": 10,
    }


def test_score_article_uses_default_and_explicit_threshold() -> None:
    scored_with_default = score_article(_threshold_article(), _threshold_source(), [])
    scored_with_higher_threshold = score_article(
        _threshold_article(),
        _threshold_source(),
        [],
        threshold=3.0,
    )

    assert scored_with_default["score"] == 2.75
    assert scored_with_default["relevance"] == 1
    assert scored_with_higher_threshold["score"] == 2.75
    assert scored_with_higher_threshold["relevance"] == 0


def test_score_article_uses_rounded_score_for_threshold_boundary() -> None:
    article = {
        **_threshold_article(),
        "url": "https://example.com/agent-rounded-threshold",
        "raw": {"likes_count": 24.96},
    }

    scored = score_article(article, _threshold_source(), [], threshold=3.0)

    assert scored["score"] == 3.0
    assert scored["relevance"] == 1


def test_collect_uses_configured_score_threshold_for_relevance_and_hits(
    tmp_path,
    monkeypatch,
) -> None:
    conn = db.connect(tmp_path / "research.db")
    db.init_db(conn)
    db.seed_source(conn, _threshold_source())
    conn.commit()

    monkeypatch.setattr(
        "ai_researcher.collector.fetch_source",
        lambda source: [_threshold_article()],
    )

    details = collect(
        conn,
        {"reporting": {"score_threshold": 3.0}},
        raw_dir=tmp_path / "raw",
    )

    article = conn.execute(
        "SELECT score, relevance FROM articles WHERE url = ?",
        (_threshold_article()["url"],),
    ).fetchone()
    source = conn.execute(
        "SELECT hit_count FROM sources WHERE id = ?",
        (_threshold_source()["id"],),
    ).fetchone()

    assert details["sources"]["threshold-source"]["relevant"] == 0
    assert article["score"] == 2.75
    assert article["relevance"] == 0
    assert source["hit_count"] == 0

    conn.close()
