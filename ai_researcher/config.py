from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import db


DEFAULT_CONFIG_PATH = "config/sources.json"
DEFAULT_SCORE_THRESHOLD = 2.5


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def seed_from_config(conn, config: dict[str, Any]) -> dict[str, int]:
    source_count = 0
    keyword_count = 0
    for source in config.get("sources", []):
        db.seed_source(conn, source)
        source_count += 1
    for keyword in config.get("keywords", []):
        db.seed_keyword(conn, keyword)
        keyword_count += 1
    conn.commit()
    return {"sources": source_count, "keywords": keyword_count}


def score_threshold(config: dict[str, Any]) -> float:
    return float(config.get("reporting", {}).get("score_threshold", DEFAULT_SCORE_THRESHOLD))
