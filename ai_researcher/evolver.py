from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db
from .utils import local_today, utc_now


def evolve(conn, config: dict[str, Any], *, output_dir: str | Path = "reports/evolution") -> dict[str, Any]:
    run_id = db.begin_run(conn, "evolve")
    now = datetime.now(timezone.utc)
    actions: list[str] = []

    try:
        actions.extend(_evolve_keywords(conn, now))
        actions.extend(_evolve_candidates(conn, now))
        actions.extend(_evolve_sources(conn, now))
        actions.extend(_enforce_keyword_limit(conn, int(config.get("limits", {}).get("active_keywords", 80))))
        conn.commit()

        path = _write_evolution_report(output_dir, actions)
        details = {"actions": len(actions), "path": str(path)}
        db.finish_run(conn, run_id, "ok", details)
        return details
    except Exception as exc:
        conn.rollback()
        db.finish_run(conn, run_id, "failed", {"error": str(exc)})
        raise


def _evolve_keywords(conn, now: datetime) -> list[str]:
    actions: list[str] = []
    rows = conn.execute("SELECT * FROM keywords").fetchall()
    for row in rows:
        status = row["status"]
        created = _parse(row["created_at"]) or now
        last_adopted = _parse(row["last_adopted_at"])

        if status == "candidate" and row["hit_count"] >= 3 and row["adopted_count"] >= 1:
            _set_keyword_status(conn, row["term"], "active")
            actions.append(f"Promoted keyword `{row['term']}` to active.")
        elif status == "candidate" and now - created > timedelta(days=30) and row["adopted_count"] == 0:
            _set_keyword_status(conn, row["term"], "stopped")
            actions.append(f"Stopped stale keyword candidate `{row['term']}`.")
        elif status == "active" and last_adopted and now - last_adopted > timedelta(days=30):
            _set_keyword_status(conn, row["term"], "low_priority")
            actions.append(f"Moved inactive keyword `{row['term']}` to low_priority.")
        elif status == "low_priority" and last_adopted and now - last_adopted <= timedelta(days=14):
            _set_keyword_status(conn, row["term"], "active")
            actions.append(f"Restored keyword `{row['term']}` to active.")
    return actions


def _evolve_candidates(conn, now: datetime) -> list[str]:
    actions: list[str] = []
    rows = conn.execute("SELECT * FROM candidates").fetchall()
    for row in rows:
        status = row["status"]
        first_seen = _parse(row["first_seen_at"]) or now
        value = row["value"]
        kind = row["kind"]
        required_hits, required_adoptions = _candidate_thresholds(kind)
        if status == "candidate" and row["hit_count"] >= required_hits and row["adopted_count"] >= required_adoptions:
            conn.execute(
                "UPDATE candidates SET status = 'active' WHERE kind = ? AND value = ?",
                (kind, value),
            )
            if kind == "keyword":
                _upsert_keyword_status(conn, value, "active")
            actions.append(f"Promoted {kind} candidate `{value}` to active.")
        elif status == "candidate" and now - first_seen > timedelta(days=30) and row["adopted_count"] == 0:
            conn.execute(
                "UPDATE candidates SET status = 'stopped' WHERE kind = ? AND value = ?",
                (kind, value),
            )
            if kind == "keyword":
                _upsert_keyword_status(conn, value, "stopped")
            actions.append(f"Stopped stale {kind} candidate `{value}`.")
    return actions


def _candidate_thresholds(kind: str) -> tuple[int, int]:
    if kind == "keyword":
        return 3, 1
    if kind == "domain":
        return 5, 2
    if kind == "author":
        return 3, 2
    return 3, 1


def _evolve_sources(conn, now: datetime) -> list[str]:
    actions: list[str] = []
    rows = conn.execute("SELECT * FROM sources").fetchall()
    for row in rows:
        status = row["status"]
        last_adopted = _parse(row["last_adopted_at"])
        created = _parse(row["created_at"]) or now
        if status == "candidate" and row["hit_count"] >= 3 and row["adopted_count"] >= 1:
            _set_source_status(conn, row["id"], "active")
            actions.append(f"Promoted source `{row['name']}` to active.")
        elif status == "candidate" and now - created > timedelta(days=30) and row["adopted_count"] == 0:
            _set_source_status(conn, row["id"], "stopped")
            actions.append(f"Stopped stale source `{row['name']}`.")
        elif status == "active" and last_adopted and now - last_adopted > timedelta(days=14):
            _set_source_status(conn, row["id"], "low_priority")
            actions.append(f"Moved source `{row['name']}` to low_priority.")
        elif status == "low_priority" and last_adopted and now - last_adopted <= timedelta(days=14):
            _set_source_status(conn, row["id"], "active")
            actions.append(f"Restored source `{row['name']}` to active.")
        elif status == "low_priority" and last_adopted and now - last_adopted > timedelta(days=30):
            _set_source_status(conn, row["id"], "stopped")
            actions.append(f"Stopped source `{row['name']}` after 30 days without adoption.")
    return actions


def _enforce_keyword_limit(conn, limit: int) -> list[str]:
    if limit <= 0:
        return []
    rows = conn.execute(
        """
        SELECT term
        FROM keywords
        WHERE status = 'active'
        ORDER BY adopted_count ASC, hit_count ASC, updated_at ASC
        """
    ).fetchall()
    extra = len(rows) - limit
    if extra <= 0:
        return []
    actions = []
    for row in rows[:extra]:
        _set_keyword_status(conn, row["term"], "low_priority")
        actions.append(f"Moved keyword `{row['term']}` to low_priority due to active keyword cap.")
    return actions


def _set_keyword_status(conn, term: str, status: str) -> None:
    conn.execute("UPDATE keywords SET status = ?, updated_at = ? WHERE term = ?", (status, utc_now(), term))


def _upsert_keyword_status(conn, term: str, status: str) -> None:
    now = utc_now()
    row = conn.execute("SELECT term FROM keywords WHERE lower(term) = lower(?) LIMIT 1", (term,)).fetchone()
    if row:
        conn.execute("UPDATE keywords SET status = ?, updated_at = ? WHERE term = ?", (status, now, row["term"]))
        return
    conn.execute(
        "INSERT INTO keywords(term, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (term, status, now, now),
    )


def _set_source_status(conn, source_id: str, status: str) -> None:
    conn.execute("UPDATE sources SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), source_id))


def _write_evolution_report(output_dir: str | Path, actions: list[str]) -> Path:
    date = local_today()
    path = Path(output_dir) / f"{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Evolution Report: {date}", ""]
    if actions:
        lines.extend(f"- {action}" for action in actions)
    else:
        lines.append("No status changes.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
