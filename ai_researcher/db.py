from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .utils import canonical_url, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  url TEXT,
  query TEXT,
  category TEXT NOT NULL DEFAULT 'general',
  status TEXT NOT NULL DEFAULT 'active',
  priority REAL NOT NULL DEFAULT 1.0,
  max_items INTEGER NOT NULL DEFAULT 30,
  hit_count INTEGER NOT NULL DEFAULT 0,
  adopted_count INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT,
  last_adopted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS keywords (
  term TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'active',
  hit_count INTEGER NOT NULL DEFAULT 0,
  adopted_count INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT,
  last_adopted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  summary TEXT,
  author TEXT,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0,
  relevance INTEGER NOT NULL DEFAULT 0,
  reasons TEXT NOT NULL DEFAULT '[]',
  matched_keywords TEXT NOT NULL DEFAULT '[]',
  tags TEXT NOT NULL DEFAULT '[]',
  cluster_key TEXT NOT NULL,
  adopted INTEGER NOT NULL DEFAULT 0,
  adopted_at TEXT,
  selection_method TEXT,
  selection_reason TEXT,
  translated_title TEXT,
  translated_summary TEXT,
  raw TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_articles_period
  ON articles(COALESCE(published_at, fetched_at), score);
CREATE INDEX IF NOT EXISTS idx_articles_source
  ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_cluster
  ON articles(cluster_key);

CREATE TABLE IF NOT EXISTS candidates (
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate',
  hit_count INTEGER NOT NULL DEFAULT 0,
  adopted_count INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_adopted_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(kind, value)
);

CREATE TABLE IF NOT EXISTS candidate_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  article_id TEXT,
  event_type TEXT NOT NULL,
  event_at TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  details TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS report_items (
  report_key TEXT NOT NULL,
  article_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  selection_method TEXT NOT NULL,
  selection_reason TEXT,
  translated_title TEXT,
  translated_summary TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(report_key, article_id),
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS drive_files (
  local_path TEXT PRIMARY KEY,
  period TEXT NOT NULL,
  label TEXT NOT NULL,
  drive_file_id TEXT,
  web_view_link TEXT,
  mime_type TEXT,
  status TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS source_discoveries (
  id TEXT PRIMARY KEY,
  discovered_at TEXT NOT NULL,
  search_query TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  domain TEXT,
  suggested_feed_url TEXT,
  source_type TEXT,
  category TEXT,
  reason TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  source_id TEXT,
  grounding_queries TEXT NOT NULL DEFAULT '[]',
  grounding_sources TEXT NOT NULL DEFAULT '[]',
  metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_source_discoveries_status
  ON source_discoveries(status, discovered_at);
CREATE INDEX IF NOT EXISTS idx_source_discoveries_domain
  ON source_discoveries(domain);

CREATE TABLE IF NOT EXISTS article_verifications (
  report_key TEXT NOT NULL,
  article_id TEXT NOT NULL,
  status TEXT NOT NULL,
  primary_source_url TEXT,
  primary_source_title TEXT,
  verification_summary TEXT,
  confidence REAL,
  checked_at TEXT NOT NULL,
  grounding_queries TEXT NOT NULL DEFAULT '[]',
  grounding_sources TEXT NOT NULL DEFAULT '[]',
  metadata TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(report_key, article_id),
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE INDEX IF NOT EXISTS idx_article_verifications_status
  ON article_verifications(status, checked_at);

CREATE TABLE IF NOT EXISTS article_feedback (
  report_key TEXT NOT NULL,
  article_id TEXT NOT NULL,
  discord_user_id TEXT NOT NULL,
  rating TEXT NOT NULL,
  comment TEXT,
  message_id TEXT,
  channel_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(report_key, article_id, discord_user_id),
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE INDEX IF NOT EXISTS idx_article_feedback_article
  ON article_feedback(article_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_article_feedback_rating
  ON article_feedback(rating, updated_at);

CREATE TABLE IF NOT EXISTS article_feedback_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_key TEXT NOT NULL,
  article_id TEXT NOT NULL,
  discord_user_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  rating TEXT,
  comment TEXT,
  message_id TEXT,
  channel_id TEXT,
  event_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE INDEX IF NOT EXISTS idx_article_feedback_events_article
  ON article_feedback_events(article_id, event_at);
CREATE INDEX IF NOT EXISTS idx_article_feedback_events_report
  ON article_feedback_events(report_key, event_at);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    article_columns = {row["name"] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
    for column, ddl in {
        "selection_method": "ALTER TABLE articles ADD COLUMN selection_method TEXT",
        "selection_reason": "ALTER TABLE articles ADD COLUMN selection_reason TEXT",
        "translated_title": "ALTER TABLE articles ADD COLUMN translated_title TEXT",
        "translated_summary": "ALTER TABLE articles ADD COLUMN translated_summary TEXT",
    }.items():
        if column not in article_columns:
            conn.execute(ddl)


def begin_run(conn: sqlite3.Connection, run_type: str, details: dict[str, Any] | None = None) -> int:
    now = utc_now()
    cur = conn.execute(
        "INSERT INTO runs(run_type, started_at, details) VALUES (?, ?, ?)",
        (run_type, now, json.dumps(details or {}, ensure_ascii=False)),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str = "ok",
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, details = ? WHERE id = ?",
        (utc_now(), status, json.dumps(details or {}, ensure_ascii=False), run_id),
    )
    conn.commit()


def upsert_drive_file(
    conn: sqlite3.Connection,
    *,
    local_path: str,
    period: str,
    label: str,
    drive_file_id: str | None,
    web_view_link: str | None,
    mime_type: str | None,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO drive_files(
          local_path, period, label, drive_file_id, web_view_link, mime_type,
          status, synced_at, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(local_path) DO UPDATE SET
          period = excluded.period,
          label = excluded.label,
          drive_file_id = excluded.drive_file_id,
          web_view_link = excluded.web_view_link,
          mime_type = excluded.mime_type,
          status = excluded.status,
          synced_at = excluded.synced_at,
          metadata = excluded.metadata
        """,
        (
            local_path,
            period,
            label,
            drive_file_id,
            web_view_link,
            mime_type,
            status,
            now,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )


def upsert_source_discovery(
    conn: sqlite3.Connection,
    *,
    discovery_id: str,
    search_query: str,
    title: str,
    url: str,
    domain: str | None,
    suggested_feed_url: str | None,
    source_type: str | None,
    category: str | None,
    reason: str | None,
    confidence: float,
    status: str,
    source_id: str | None = None,
    grounding_queries: list[str] | None = None,
    grounding_sources: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO source_discoveries(
          id, discovered_at, search_query, title, url, domain, suggested_feed_url,
          source_type, category, reason, confidence, status, source_id,
          grounding_queries, grounding_sources, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          discovered_at = excluded.discovered_at,
          search_query = excluded.search_query,
          title = excluded.title,
          url = excluded.url,
          domain = excluded.domain,
          suggested_feed_url = excluded.suggested_feed_url,
          source_type = excluded.source_type,
          category = excluded.category,
          reason = excluded.reason,
          confidence = excluded.confidence,
          status = excluded.status,
          source_id = excluded.source_id,
          grounding_queries = excluded.grounding_queries,
          grounding_sources = excluded.grounding_sources,
          metadata = excluded.metadata
        """,
        (
            discovery_id,
            now,
            search_query,
            title,
            url,
            domain,
            suggested_feed_url,
            source_type,
            category,
            reason,
            float(confidence),
            status,
            source_id,
            json.dumps(grounding_queries or [], ensure_ascii=False),
            json.dumps(grounding_sources or [], ensure_ascii=False),
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )


def upsert_article_verification(
    conn: sqlite3.Connection,
    *,
    report_key: str,
    article_id: str,
    status: str,
    primary_source_url: str | None = None,
    primary_source_title: str | None = None,
    verification_summary: str | None = None,
    confidence: float | None = None,
    grounding_queries: list[str] | None = None,
    grounding_sources: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO article_verifications(
          report_key, article_id, status, primary_source_url, primary_source_title,
          verification_summary, confidence, checked_at, grounding_queries,
          grounding_sources, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_key, article_id) DO UPDATE SET
          status = excluded.status,
          primary_source_url = excluded.primary_source_url,
          primary_source_title = excluded.primary_source_title,
          verification_summary = excluded.verification_summary,
          confidence = excluded.confidence,
          checked_at = excluded.checked_at,
          grounding_queries = excluded.grounding_queries,
          grounding_sources = excluded.grounding_sources,
          metadata = excluded.metadata
        """,
        (
            report_key,
            article_id,
            status,
            primary_source_url,
            primary_source_title,
            verification_summary,
            confidence,
            now,
            json.dumps(grounding_queries or [], ensure_ascii=False),
            json.dumps(grounding_sources or [], ensure_ascii=False),
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )


def get_article_verifications(conn: sqlite3.Connection, report_key: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM article_verifications
        WHERE report_key = ?
        """,
        (report_key,),
    ).fetchall()
    return {row["article_id"]: _row_to_plain_dict(row) for row in rows}


def find_source_by_url(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    cleaned = canonical_url(url)
    row = conn.execute(
        "SELECT * FROM sources WHERE lower(url) IN (lower(?), lower(?)) LIMIT 1",
        (url, cleaned),
    ).fetchone()
    if row:
        return row
    for candidate in conn.execute("SELECT * FROM sources WHERE url IS NOT NULL").fetchall():
        if canonical_url(candidate["url"]) == cleaned:
            return candidate
    return None


def source_id_exists(conn: sqlite3.Connection, source_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM sources WHERE id = ? LIMIT 1", (source_id,)).fetchone()
    return row is not None


def seed_source(conn: sqlite3.Connection, source: dict[str, Any]) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO sources(
          id, name, type, url, query, category, status, priority, max_items,
          created_at, updated_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          type = excluded.type,
          url = excluded.url,
          query = excluded.query,
          category = excluded.category,
          priority = excluded.priority,
          max_items = excluded.max_items,
          notes = excluded.notes,
          updated_at = excluded.updated_at
        """,
        (
            source["id"],
            source["name"],
            source["type"],
            source.get("url"),
            source.get("query"),
            source.get("category", "general"),
            source.get("status", "active"),
            float(source.get("priority", 1.0)),
            int(source.get("max_items", 30)),
            now,
            now,
            source.get("notes"),
        ),
    )


def seed_keyword(conn: sqlite3.Connection, term: str, status: str = "active") -> None:
    now = utc_now()
    cleaned = term.strip()
    if not cleaned:
        return
    conn.execute(
        """
        INSERT INTO keywords(term, status, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(term) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (cleaned, status, now, now),
    )


def get_sources(conn: sqlite3.Connection, statuses: Iterable[str] = ("active", "candidate", "low_priority")) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in statuses)
    return list(
        conn.execute(
            f"SELECT * FROM sources WHERE status IN ({placeholders}) ORDER BY priority DESC, name",
            tuple(statuses),
        )
    )


def get_keywords(conn: sqlite3.Connection, statuses: Iterable[str] = ("active", "candidate")) -> list[str]:
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT term FROM keywords WHERE status IN ({placeholders}) ORDER BY adopted_count DESC, hit_count DESC, term",
        tuple(statuses),
    )
    return [row["term"] for row in rows]


def upsert_article(conn: sqlite3.Connection, article: dict[str, Any]) -> bool:
    cur = conn.execute(
        """
        INSERT INTO articles(
          id, source_id, url, title, summary, author, published_at, fetched_at,
          score, relevance, reasons, matched_keywords, tags, cluster_key, raw
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
          source_id = excluded.source_id,
          title = excluded.title,
          summary = excluded.summary,
          author = excluded.author,
          published_at = COALESCE(articles.published_at, excluded.published_at),
          fetched_at = excluded.fetched_at,
          score = MAX(articles.score, excluded.score),
          relevance = MAX(articles.relevance, excluded.relevance),
          reasons = excluded.reasons,
          matched_keywords = excluded.matched_keywords,
          tags = excluded.tags,
          cluster_key = excluded.cluster_key,
          raw = excluded.raw
        """,
        (
            article["id"],
            article["source_id"],
            article["url"],
            article["title"],
            article.get("summary"),
            article.get("author"),
            article.get("published_at"),
            article["fetched_at"],
            float(article.get("score", 0)),
            int(article.get("relevance", 0)),
            json.dumps(article.get("reasons", []), ensure_ascii=False),
            json.dumps(article.get("matched_keywords", []), ensure_ascii=False),
            json.dumps(article.get("tags", []), ensure_ascii=False),
            article["cluster_key"],
            json.dumps(article.get("raw", {}), ensure_ascii=False),
        ),
    )
    return cur.rowcount > 0


def update_source_hits(conn: sqlite3.Connection, source_id: str, relevant_hits: int) -> None:
    if relevant_hits <= 0:
        return
    now = utc_now()
    conn.execute(
        """
        UPDATE sources
        SET hit_count = hit_count + ?,
            last_seen_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (relevant_hits, now, now, source_id),
    )


def record_keyword_hits(conn: sqlite3.Connection, terms: Iterable[str]) -> None:
    now = utc_now()
    for term in terms:
        term = _resolve_keyword_term(conn, _normalize_candidate_value("keyword", term))
        conn.execute(
            """
            INSERT INTO keywords(term, status, hit_count, last_seen_at, created_at, updated_at)
            VALUES (?, 'candidate', 1, ?, ?, ?)
            ON CONFLICT(term) DO UPDATE SET
              hit_count = hit_count + 1,
              last_seen_at = excluded.last_seen_at,
              updated_at = excluded.updated_at
            """,
            (term, now, now, now),
        )


def record_candidate(
    conn: sqlite3.Connection,
    kind: str,
    value: str,
    article_id: str | None,
    *,
    adopted: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    cleaned = _normalize_candidate_value(kind, value)
    if not cleaned:
        return
    now = utc_now()
    adopted_count = 1 if adopted else 0
    candidate_status = "active" if kind == "keyword" and _keyword_is_active(conn, cleaned) else "candidate"
    conn.execute(
        """
        INSERT INTO candidates(
          kind, value, status, hit_count, adopted_count, first_seen_at, last_seen_at,
          last_adopted_at, metadata
        )
        VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
        ON CONFLICT(kind, value) DO UPDATE SET
          status = CASE
            WHEN excluded.status = 'active' THEN 'active'
            ELSE candidates.status
          END,
          hit_count = hit_count + 1,
          adopted_count = adopted_count + excluded.adopted_count,
          last_seen_at = excluded.last_seen_at,
          last_adopted_at = COALESCE(excluded.last_adopted_at, candidates.last_adopted_at),
          metadata = CASE
            WHEN excluded.metadata != '{}' THEN excluded.metadata
            ELSE candidates.metadata
          END
        """,
        (
            kind,
            cleaned,
            candidate_status,
            adopted_count,
            now,
            now,
            now if adopted else None,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    conn.execute(
        """
        INSERT INTO candidate_events(kind, value, article_id, event_type, event_at, details)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            cleaned,
            article_id,
            "adopted" if adopted else "seen",
            now,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )


def _normalize_candidate_value(kind: str, value: str) -> str:
    cleaned = str(value).strip()
    if kind == "keyword":
        return cleaned.lower()
    return cleaned


def _resolve_keyword_term(conn: sqlite3.Connection, term: str) -> str:
    cleaned = _normalize_candidate_value("keyword", term)
    row = conn.execute("SELECT term FROM keywords WHERE lower(term) = lower(?) LIMIT 1", (cleaned,)).fetchone()
    return row["term"] if row else cleaned


def _keyword_is_active(conn: sqlite3.Connection, term: str) -> bool:
    row = conn.execute(
        "SELECT status FROM keywords WHERE lower(term) = lower(?) LIMIT 1",
        (_normalize_candidate_value("keyword", term),),
    ).fetchone()
    return bool(row and row["status"] == "active")


def mark_articles_adopted(
    conn: sqlite3.Connection,
    selected_articles: list[dict[str, Any]],
    *,
    report_key: str,
) -> int:
    if not selected_articles:
        return 0

    now = utc_now()
    article_ids = [article["id"] for article in selected_articles]
    existing_report_items = {
        row["article_id"]
        for row in conn.execute(
            f"""
            SELECT article_id
            FROM report_items
            WHERE report_key = ?
              AND article_id IN ({','.join('?' for _ in article_ids)})
            """,
            [report_key, *article_ids],
        )
    }
    newly_adopted = [article_id for article_id in article_ids if article_id not in existing_report_items]

    for rank, article in enumerate(selected_articles, start=1):
        conn.execute(
            """
            INSERT INTO report_items(
              report_key, article_id, rank, selection_method, selection_reason,
              translated_title, translated_summary, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_key, article_id) DO UPDATE SET
              rank = excluded.rank,
              selection_method = excluded.selection_method,
              selection_reason = excluded.selection_reason,
              translated_title = excluded.translated_title,
              translated_summary = excluded.translated_summary
            """,
            (
                report_key,
                article["id"],
                rank,
                article.get("selection_method") or "unknown",
                article.get("selection_reason"),
                article.get("translated_title"),
                article.get("translated_summary"),
                now,
            ),
        )
        conn.execute(
            """
            UPDATE articles
            SET selection_method = ?,
                selection_reason = ?,
                translated_title = ?,
                translated_summary = ?
            WHERE id = ?
            """,
            (
                article.get("selection_method") or "unknown",
                article.get("selection_reason"),
                article.get("translated_title"),
                article.get("translated_summary"),
                article["id"],
            ),
        )

    conn.execute(
        f"""
        UPDATE articles
        SET adopted = 1, adopted_at = COALESCE(adopted_at, ?)
        WHERE id IN ({','.join('?' for _ in article_ids)})
        """,
        [now, *article_ids],
    )

    if not newly_adopted:
        return 0

    rows = conn.execute(
        f"""
        SELECT a.id, a.source_id, a.url, a.author, a.matched_keywords
        FROM articles a
        WHERE a.id IN ({','.join('?' for _ in newly_adopted)})
        """,
        newly_adopted,
    ).fetchall()

    source_counts: dict[str, int] = {}
    for row in rows:
        source_counts[row["source_id"]] = source_counts.get(row["source_id"], 0) + 1
        for term in json.loads(row["matched_keywords"] or "[]"):
            term = _resolve_keyword_term(conn, term)
            conn.execute(
                """
                UPDATE keywords
                SET adopted_count = adopted_count + 1,
                    last_adopted_at = ?,
                    updated_at = ?
                WHERE lower(term) = lower(?)
                """,
                (now, now, term),
            )
            record_candidate(conn, "keyword", term, row["id"], adopted=True)
        if row["author"]:
            record_candidate(conn, "author", row["author"], row["id"], adopted=True)

    for source_id, count in source_counts.items():
        conn.execute(
            """
            UPDATE sources
            SET adopted_count = adopted_count + ?,
                last_adopted_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (count, now, now, source_id),
        )

    return len(newly_adopted)


def _row_to_plain_dict(row: sqlite3.Row) -> dict[str, Any]:
    value = {key: row[key] for key in row.keys()}
    for key in ("grounding_queries", "grounding_sources", "metadata"):
        try:
            value[key] = json.loads(value.get(key) or "[]")
        except (TypeError, json.JSONDecodeError):
            value[key] = [] if key != "metadata" else {}
    return value
