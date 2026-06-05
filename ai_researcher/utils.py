from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


USER_AGENT = os.environ.get(
    "AI_RESEARCHER_USER_AGENT",
    "ai-researcher/0.1 (+local personal research feed reader)",
)


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_today() -> str:
    tz = ZoneInfo(os.environ.get("AI_RESEARCH_TZ", "Asia/Tokyo"))
    return datetime.now(tz).date().isoformat()


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, IndexError):
        pass

    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    stripper = _HTMLStripper()
    try:
        stripper.feed(value)
        text = stripper.text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return normalize_space(text)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def http_get_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = Request(url, headers=request_headers)
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        encoding = resp.headers.get_content_charset() or "utf-8"
        return data.decode(encoding, errors="replace")


def http_get_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    return json.loads(http_get_text(url, headers=headers, timeout=timeout))


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower()
        not in {
            "fbclid",
            "gclid",
            "yclid",
            "mc_cid",
            "mc_eid",
            "ref",
            "source",
        }
    ]
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path) or "/"
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            netloc,
            path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


def url_domain(url: str | None) -> str:
    if not url:
        return ""
    domain = urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


def stable_id(*parts: str) -> str:
    value = "\n".join(parts)
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def cluster_key(title: str, url: str) -> str:
    domain = url_domain(url)
    text = re.sub(r"https?://\S+", " ", title.lower())
    text = re.sub(r"[^a-z0-9一-龯ぁ-んァ-ンー]+", " ", text)
    tokens = [t for t in text.split() if len(t) > 2 and t not in _TITLE_STOPWORDS]
    if not tokens:
        return stable_id(domain, canonical_url(url))[:16]
    return stable_id(" ".join(tokens[:12]))[:16]


def sleep_for_rate_limit(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


_TITLE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "about",
    "using",
    "how",
    "what",
    "why",
    "when",
    "where",
    "new",
    "release",
    "announcing",
    "introducing",
    "update",
    "guide",
    "tips",
    "zenn",
    "qiita",
}
