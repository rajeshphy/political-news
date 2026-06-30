from __future__ import annotations

import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

try:
    from .common import IST, NewsItem, clean_text
    from .directlink import resolve_direct_link
    from .filter import (
        assign_ids,
        dedupe_items,
        filter_excluded_items,
        filter_fresh_items,
        filter_relevant_items,
        select_top_story_groups,
    )
except ImportError:
    from common import IST, NewsItem, clean_text
    from directlink import resolve_direct_link
    from filter import (
        assign_ids,
        dedupe_items,
        filter_excluded_items,
        filter_fresh_items,
        filter_relevant_items,
        select_top_story_groups,
    )


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PoliticalBrief/1.0 (+https://github.com/rajeshphy/political-news)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-IN,en;q=0.9,hi-IN;q=0.8",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        content_type = response.headers.get("content-type", "")
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", content_type, flags=re.I)

        if match:
            charset = match.group(1)

        return raw.decode(charset, errors="replace")


def default_source_weight(source_name: str, section: str) -> int:
    name = source_name.lower()

    if any(source in name for source in ("parliament", "election commission", "supreme court")):
        return 4

    if any(source in name for source in ("pib", "cabinet", "national politics", "policy")):
        return 3

    return 1


def parse_feed_datetime(value: str) -> datetime | None:
    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed

    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def format_item_date(value: datetime | None) -> str:
    if not value:
        return "unknown"

    return value.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def first_text(entry, names: tuple[str, ...]) -> str:
    for name in names:
        value = entry.findtext(name)

        if value:
            return clean_text(value)

    return ""


def collect_from_rss(section: str, source_config: dict) -> list[NewsItem]:
    source_name = source_config.get("name", "News source")
    source_weight = int(source_config.get("weight", default_source_weight(source_name, section)))
    feed = fetch_text(source_config["url"])
    root = ET.fromstring(feed)
    items: list[NewsItem] = []
    prefix = "I" if section == "india" else "W"

    for entry in root.findall(".//item"):
        title = clean_text(entry.findtext("title"))
        link = clean_text(entry.findtext("link"))
        summary = clean_text(entry.findtext("description"))
        source = clean_text(entry.findtext("source")) or source_name
        published_at = parse_feed_datetime(clean_text(entry.findtext("pubDate")))

        if not title or not link:
            continue

        items.append(
            NewsItem(
                section=section,
                item_id="",
                title=title,
                url=link,
                source=source,
                summary=summary,
                source_weight=source_weight,
                published=format_item_date(published_at),
                published_at=published_at,
            )
        )

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for entry in root.findall(".//atom:entry", ns):
        title = clean_text(entry.findtext("atom:title", default="", namespaces=ns))
        link = ""

        for node in entry.findall("atom:link", ns):
            href = node.attrib.get("href", "")
            rel = node.attrib.get("rel", "alternate")

            if href and rel in {"alternate", ""}:
                link = href
                break

        published_at = parse_feed_datetime(
            clean_text(entry.findtext("atom:published", default="", namespaces=ns))
            or clean_text(entry.findtext("atom:updated", default="", namespaces=ns))
        )
        summary = (
            clean_text(entry.findtext("atom:summary", default="", namespaces=ns))
            or clean_text(entry.findtext("atom:content", default="", namespaces=ns))
        )

        if not title or not link:
            continue

        items.append(
            NewsItem(
                section=section,
                item_id="",
                title=title,
                url=link,
                source=source_name,
                summary=summary,
                source_weight=source_weight,
                published=format_item_date(published_at),
                published_at=published_at,
            )
        )

    for index, item in enumerate(items, 1):
        item.item_id = f"{prefix}{index}"

    return items


def resolve_selected_links(items: list[NewsItem], settings: dict) -> list[NewsItem]:
    if not items:
        return items

    max_workers = max(1, int(settings.get("direct_link_workers", 8)))
    urls = [item.url for item in items]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        resolved_urls = list(executor.map(resolve_direct_link, urls))

    for item, resolved_url in zip(items, resolved_urls):
        item.url = resolved_url.strip() or item.url

    return items


def collect_news(config: dict) -> list[NewsItem]:
    all_items: list[NewsItem] = []
    settings = config.get("settings", {})

    for section in ("india", "world"):
        section_items: list[NewsItem] = []

        for source in config.get("sources", {}).get(section, []):
            if source.get("type") != "rss" or not source.get("url"):
                continue

            try:
                section_items.extend(collect_from_rss(section, source))

            except Exception as exc:
                print(
                    f"Warning: failed to fetch {source.get('name', source.get('url'))}: {exc}",
                    file=sys.stderr,
                )

        relevant_items = filter_relevant_items(section, section_items, settings)
        useful_items = filter_excluded_items(relevant_items, settings)
        fresh_items = filter_fresh_items(useful_items, settings)
        candidate_items = dedupe_items(fresh_items)
        selected_groups = select_top_story_groups(section, candidate_items, settings)
        selected_items = [item for group in selected_groups for item in group]
        all_items.extend(assign_ids(section, resolve_selected_links(selected_items, settings)))

    return all_items
