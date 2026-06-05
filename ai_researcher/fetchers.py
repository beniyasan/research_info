from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from .utils import canonical_url, http_get_json, http_get_text, parse_date, strip_html


def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    source_type = source["type"].lower()
    max_items = int(source.get("max_items") or 30)
    if source.get("status") == "low_priority":
        max_items = max(5, max_items // 2)

    if source_type == "rss":
        return fetch_rss(source["url"], max_items=max_items)
    if source_type == "qiita":
        return fetch_qiita(source.get("query") or "", max_items=max_items)
    if source_type == "hn_algolia":
        return fetch_hackernews_algolia(source.get("query") or "", max_items=max_items)
    if source_type == "html_index":
        return fetch_html_index(source, max_items=max_items)
    if source_type == "deeplearning_batch":
        return fetch_deeplearning_batch(source, max_items=max_items)

    raise ValueError(f"Unsupported source type: {source_type}")


def fetch_rss(url: str, *, max_items: int = 30) -> list[dict[str, Any]]:
    text = http_get_text(url)
    root = ET.fromstring(text)

    if _local_name(root.tag) == "rss":
        channel = _first_child(root, "channel")
        entries = list(channel) if channel is not None else []
        entries = [entry for entry in entries if _local_name(entry.tag) == "item"]
        return [_rss_item_to_article(item) for item in entries[:max_items]]

    if _local_name(root.tag) == "feed":
        entries = [entry for entry in list(root) if _local_name(entry.tag) == "entry"]
        return [_atom_entry_to_article(entry) for entry in entries[:max_items]]

    return []


def fetch_qiita(query: str, *, max_items: int = 30) -> list[dict[str, Any]]:
    params = urlencode({"query": query, "per_page": min(max_items, 100), "page": 1})
    headers: dict[str, str] = {}
    token = os.environ.get("QIITA_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    items = http_get_json(f"https://qiita.com/api/v2/items?{params}", headers=headers)

    articles = []
    for item in items[:max_items]:
        tags = [tag.get("name", "") for tag in item.get("tags", []) if tag.get("name")]
        user = item.get("user") or {}
        summary = strip_html(item.get("rendered_body") or item.get("body") or "")[:900]
        articles.append(
            {
                "title": item.get("title") or "(untitled)",
                "url": canonical_url(item.get("url")),
                "summary": summary,
                "author": user.get("id"),
                "published_at": parse_date(item.get("created_at")),
                "tags": tags,
                "raw": {
                    "likes_count": item.get("likes_count"),
                    "stocks_count": item.get("stocks_count"),
                    "comments_count": item.get("comments_count"),
                },
            }
        )
    return articles


def fetch_hackernews_algolia(query: str, *, max_items: int = 30) -> list[dict[str, Any]]:
    params = urlencode({"query": query, "tags": "story", "hitsPerPage": min(max_items, 100)})
    data = http_get_json(f"https://hn.algolia.com/api/v1/search_by_date?{params}")

    articles = []
    for hit in data.get("hits", [])[:max_items]:
        title = hit.get("title") or hit.get("story_title") or "(untitled)"
        url = hit.get("url") or hit.get("story_url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        articles.append(
            {
                "title": title,
                "url": canonical_url(url),
                "summary": title,
                "author": hit.get("author"),
                "published_at": parse_date(hit.get("created_at")),
                "tags": ["hacker-news"],
                "raw": {
                    "objectID": hit.get("objectID"),
                    "points": hit.get("points"),
                    "num_comments": hit.get("num_comments"),
                    "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                },
            }
        )
    return articles


def fetch_html_index(source: dict[str, Any], *, max_items: int = 30) -> list[dict[str, Any]]:
    index_url = source["url"]
    html = http_get_text(index_url, headers={"User-Agent": "Mozilla/5.0 ai-researcher/0.1"})
    parser = _AnchorExtractor()
    parser.feed(html)

    include_paths = list(source.get("include_paths") or [])
    exclude_paths = list(source.get("exclude_paths") or [])
    same_domain = bool(source.get("same_domain", True))
    base_domain = urlparse(index_url).netloc.lower()
    tags = [str(tag) for tag in source.get("tags", [])]

    by_url: dict[str, dict[str, Any]] = {}
    for href, text in parser.links:
        title_text = _clean_anchor_text(text)
        if _is_generic_anchor(title_text):
            continue

        full_url = canonical_url(urljoin(index_url, href))
        if not full_url:
            continue

        parsed = urlparse(full_url)
        if same_domain and parsed.netloc.lower() != base_domain:
            continue
        if include_paths and not any(parsed.path.startswith(path) for path in include_paths):
            continue
        if exclude_paths and any(parsed.path.startswith(path) for path in exclude_paths):
            continue

        published = _published_from_text(title_text)
        title = _title_from_anchor_text(title_text)
        if _is_generic_anchor(title) or len(title) < 8:
            continue

        article = {
            "title": title,
            "url": full_url,
            "summary": title_text[:900],
            "author": source.get("name") or "",
            "published_at": published,
            "tags": tags,
            "raw": {"index_url": index_url, "source_type": "html_index"},
        }
        existing = by_url.get(full_url)
        source_name = source.get("name") or ""
        existing_is_source_title = bool(existing and _looks_like_source_title(existing["title"], source_name))
        article_is_source_title = _looks_like_source_title(article["title"], source_name)
        if (
            existing is None
            or (existing_is_source_title and not article_is_source_title)
            or (
                existing_is_source_title == article_is_source_title
                and len(article["summary"]) > len(existing["summary"])
            )
        ):
            by_url[full_url] = article

    return list(by_url.values())[:max_items]


def fetch_deeplearning_batch(source: dict[str, Any], *, max_items: int = 20) -> list[dict[str, Any]]:
    index_url = source["url"]
    html = http_get_text(index_url, headers={"User-Agent": "Mozilla/5.0 ai-researcher/0.1"})
    tags = [str(tag) for tag in source.get("tags", [])]
    articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    card_re = re.compile(
        r'(?P<date_anchor><a[^>]+href="(?P<href>/the-batch/tag/[^"]+)"[^>]*>'
        r".{0,300}?"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}"
        r".{0,300}?</a>)"
        r"\s*<h2[^>]*>(?P<title>.*?)</h2>"
        r"\s*<div[^>]*>(?P<summary>.*?)</div>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in card_re.finditer(html):
        title = strip_html(match.group("title"))
        if not title or _is_generic_anchor(title):
            continue

        url = canonical_url(urljoin(index_url, match.group("href")))
        if url in seen_urls:
            continue
        seen_urls.add(url)

        date_text = strip_html(match.group("date_anchor"))
        summary = strip_html(match.group("summary")) or title
        articles.append(
            {
                "title": title,
                "url": url,
                "summary": summary[:900],
                "author": source.get("name") or "DeepLearning.AI",
                "published_at": _published_from_text(date_text),
                "tags": tags,
                "raw": {"index_url": index_url, "source_type": "deeplearning_batch"},
            }
        )
        if len(articles) >= max_items:
            break

    return articles


def _rss_item_to_article(item: ET.Element) -> dict[str, Any]:
    title = _text(item, "title") or "(untitled)"
    link = _text(item, "link") or _text(item, "guid")
    summary = _text(item, "description") or _text(item, "encoded") or _text(item, "summary")
    author = _text(item, "creator") or _text(item, "author")
    published = _text(item, "pubDate") or _text(item, "published") or _text(item, "updated")
    tags = [_clean_text(child.text) for child in item if _local_name(child.tag) == "category" and child.text]
    return {
        "title": strip_html(title),
        "url": canonical_url(link),
        "summary": strip_html(summary),
        "author": strip_html(author),
        "published_at": parse_date(published),
        "tags": tags,
        "raw": {},
    }


def _atom_entry_to_article(entry: ET.Element) -> dict[str, Any]:
    title = _text(entry, "title") or "(untitled)"
    link = ""
    for child in entry:
        if _local_name(child.tag) == "link" and child.attrib.get("href"):
            if child.attrib.get("rel", "alternate") == "alternate":
                link = child.attrib["href"]
                break
            link = link or child.attrib["href"]
    summary = _text(entry, "summary") or _text(entry, "content")
    author = ""
    for child in entry:
        if _local_name(child.tag) == "author":
            author = _text(child, "name") or _clean_text(child.text)
            break
    published = _text(entry, "published") or _text(entry, "updated")
    tags = [child.attrib.get("term", "") for child in entry if _local_name(child.tag) == "category"]
    return {
        "title": strip_html(title),
        "url": canonical_url(link),
        "summary": strip_html(summary),
        "author": strip_html(author),
        "published_at": parse_date(published),
        "tags": [tag for tag in tags if tag],
        "raw": {},
    }


def _first_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if _local_name(child.tag) == name:
            return child
    return None


def _text(parent: ET.Element, name: str) -> str:
    for child in parent:
        if _local_name(child.tag) == name:
            return _clean_text(child.text)
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_text(value: str | None) -> str:
    return value.strip() if value else ""


class _AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {key: value or "" for key, value in attrs}
        self._href = attrs_dict.get("href")
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href and data.strip():
            self._parts.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        self.links.append((self._href, " ".join(self._parts)))
        self._href = None
        self._parts = []


_GENERIC_ANCHORS = {
    "ai newsletter",
    "ai research",
    "announcements",
    "blog",
    "business",
    "company",
    "engineering",
    "featured",
    "latest news",
    "learn more",
    "newsletter",
    "product",
    "products",
    "read all news",
    "read more",
    "research",
    "resources",
    "science",
    "see more",
    "skip to footer",
    "skip to main content",
    "solutions",
    "technology",
    "view all",
    "詳しくはこちら",
}

_ARTICLE_LABELS = (
    "FEATURED",
    "Product",
    "Products",
    "Announcements",
    "Announcement",
    "Research",
    "Engineering",
    "Policy",
    "Company",
    "Solutions",
)

_MONTHS = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "sept": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}

_MONTH_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})\b",
    re.IGNORECASE,
)


def _clean_anchor_text(value: str) -> str:
    return re.sub(r"\s+", " ", strip_html(value)).strip()


def _is_generic_anchor(value: str) -> bool:
    cleaned = _clean_anchor_text(value)
    key = cleaned.lower()
    if not cleaned:
        return True
    if key in _GENERIC_ANCHORS:
        return True
    if key.startswith("skip to "):
        return True
    if len(cleaned) <= 3:
        return True
    return False


def _published_from_text(value: str) -> str | None:
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
    if iso_match:
        return parse_date(iso_match.group(1))

    match = _MONTH_DATE_RE.search(value)
    if not match:
        return None
    month = _MONTHS[match.group(1).lower()[:3]]
    return parse_date(f"{match.group(3)}-{month}-{int(match.group(2)):02d}")


def _title_from_anchor_text(value: str) -> str:
    text = _clean_anchor_text(value)
    match = _MONTH_DATE_RE.search(text)
    if match:
        before = text[: match.start()].strip()
        after = text[match.end() :].strip()
        if 8 <= len(before) <= 160:
            text = before
        elif after:
            text = after

    for label in _ARTICLE_LABELS:
        text = re.sub(rf"^{re.escape(label)}\s*", "", text).strip()
        text = re.sub(rf"\s*{re.escape(label)}$", "", text).strip()

    if len(text) > 180:
        text = text[:177] + "..."
    return text


def _looks_like_source_title(title: str, source_name: str) -> bool:
    title_key = re.sub(r"\b(news|blog|official|developer)\b", "", title.lower()).strip()
    source_key = re.sub(r"\b(news|blog|official|developer)\b", "", source_name.lower()).strip()
    return bool(title_key and source_key and (title_key == source_key or title_key in source_key))
