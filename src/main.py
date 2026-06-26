#!/usr/bin/env python3
"""Generate an English daily political news brief."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SITE_TITLE = "Political Brief"
SOURCE_CONFIG = "config/sources.yml"
GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "docs" / "_posts"
DATA = ROOT / "data"
QUOTA_FILE = DATA / "quota.json"


@dataclass
class NewsItem:
    section: str
    item_id: str
    title: str
    url: str
    source: str = ""
    source_weight: int = 1
    published: str = ""
    published_at: datetime | None = None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def read_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_simple_yaml(path: Path) -> dict:
    """Parse the small sources.yml shape used by this project."""
    config: dict = {"settings": {}, "sources": {"india": [], "world": []}}
    section = None
    group = None
    current_item = None
    block_key = None
    block_indent = 0
    block_lines: list[str] = []

    def finish_block() -> None:
        nonlocal block_key, block_lines
        if block_key:
            config["settings"][block_key] = clean_text(" ".join(block_lines))
        block_key = None
        block_lines = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_key:
            if stripped and indent > block_indent:
                block_lines.append(stripped)
                continue
            finish_block()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            section = stripped[:-1]
            group = None
            current_item = None
            continue
        if section == "settings" and ":" in stripped:
            key, value = stripped.split(":", 1)
            parsed_value = parse_yaml_value(value)
            if parsed_value in {">", ">-", "|", "|-"}:
                block_key = key.strip()
                block_indent = indent
                block_lines = []
            else:
                config["settings"][key.strip()] = parsed_value
            continue
        if section == "sources" and line.startswith("  ") and stripped.endswith(":"):
            group = stripped[:-1]
            config["sources"].setdefault(group, [])
            current_item = None
            continue
        if section == "sources" and group and stripped.startswith("- "):
            current_item = {}
            config["sources"][group].append(current_item)
            remainder = stripped[2:]
            if ":" in remainder:
                key, value = remainder.split(":", 1)
                current_item[key.strip()] = parse_yaml_value(value)
            continue
        if current_item is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_item[key.strip()] = parse_yaml_value(value)
    finish_block()
    return config


def parse_yaml_value(value: str):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value.isdigit():
        return int(value)
    return value


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PoliticalBrief/1.0 (+https://github.com/rajeshphy/political-news)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-IN,en;q=0.9,hi-IN;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
        content_type = response.headers.get("content-type", "")
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.I)
    if match:
        charset = match.group(1)
    return raw.decode(charset, errors="replace")


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
        source = clean_text(entry.findtext("source")) or source_name
        published_at = parse_feed_datetime(clean_text(entry.findtext("pubDate")))
        published = format_item_date(published_at)
        if not title or not link:
            continue
        items.append(
            NewsItem(
                section=section,
                item_id="",
                title=title,
                url=link,
                source=source,
                source_weight=source_weight,
                published=published,
                published_at=published_at,
            )
        )

    for index, item in enumerate(items, 1):
        item.item_id = f"{prefix}{index}"
    return items


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
                print(f"Warning: failed to fetch {source.get('name', source.get('url'))}: {exc}", file=sys.stderr)
        limit = int(config.get("settings", {}).get(f"{section}_limit", 24))
        relevant_items = filter_relevant_items(section, section_items, settings)
        useful_items = filter_excluded_items(relevant_items, settings)
        fresh_items = filter_fresh_items(useful_items, settings)
        candidate_items = dedupe_items(fresh_items)[:limit]
        selected_groups = select_top_story_groups(section, candidate_items, settings)
        selected_items = [item for group in selected_groups for item in group]
        all_items.extend(assign_ids(section, selected_items))
    return all_items


def config_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def filter_fresh_items(items: list[NewsItem], settings: dict) -> list[NewsItem]:
    require_today = config_bool(settings.get("require_ist_today"), True)
    allow_unknown_dates = config_bool(settings.get("allow_unknown_dates"), False)
    max_age_hours = int(settings.get("max_age_hours", 30))
    now_ist = datetime.now(IST)
    fresh: list[NewsItem] = []

    for item in items:
        if not item.published_at:
            if allow_unknown_dates:
                fresh.append(item)
            continue
        published_ist = item.published_at.astimezone(IST)
        if require_today:
            if published_ist.date() == now_ist.date():
                fresh.append(item)
            continue
        if now_ist - published_ist <= timedelta(hours=max_age_hours):
            fresh.append(item)

    return sorted(fresh, key=item_sort_key, reverse=True)


def filter_relevant_items(section: str, items: list[NewsItem], settings: dict) -> list[NewsItem]:
    keywords = section_keywords(section, settings)
    topic_keywords = domain_topic_keywords(settings)
    india_anchors = configured_keywords(
        settings,
        "india_anchor_keywords",
        (
            "india,indian,modi,prime minister modi,union government,central government,"
            "lok sabha,rajya sabha,parliament of india,election commission of india,"
            "eci,bjp,congress,nda,india bloc,supreme court"
        ),
    )
    if not keywords:
        return items
    relevant = []
    for item in items:
        haystack = f"{item.title} {item.source}".lower()
        section_match = any(keyword in haystack for keyword in keywords)
        topic_match = any(keyword in haystack for keyword in topic_keywords)
        anchor_match = any(keyword in haystack for keyword in india_anchors)
        if section_match and topic_match and anchor_match:
            relevant.append(item)
    return relevant


def filter_excluded_items(items: list[NewsItem], settings: dict) -> list[NewsItem]:
    excluded = configured_keywords(
        settings,
        "exclude_keywords",
        (
            "horoscope,astrology,photo gallery,photos,web story,viral video,recipe,"
            "lottery,result live,cricket score,match preview"
        ),
    )
    if not excluded:
        return items
    useful = []
    for item in items:
        haystack = f"{item.title} {item.source}".lower()
        if not any(keyword in haystack for keyword in excluded):
            useful.append(item)
    return useful


def section_keywords(section: str, settings: dict) -> list[str]:
    default_india = "india,indian,national,central government,parliament,lok sabha,rajya sabha,cabinet,election commission"
    default_world = (
        "policy,bill,ordinance,parliament,cabinet,election,alliance,party,governance,constitution,supreme court"
    )
    return configured_keywords(settings, f"{section}_keywords", default_india if section == "india" else default_world)


def configured_keywords(settings: dict, key: str, default: str) -> list[str]:
    raw = settings.get(key, default)
    return [clean_text(keyword).lower() for keyword in str(raw).split(",") if clean_text(keyword)]


def domain_topic_keywords(settings: dict) -> list[str]:
    return configured_keywords(
        settings,
        "politics_topic_keywords",
        (
            "parliament,lok sabha,rajya sabha,bill,ordinance,cabinet,minister,"
            "prime minister,president,election,election commission,eci,supreme court,"
            "constitution,governance,policy,alliance,party,bjp,congress,nda,india bloc"
        ),
    )


def item_sort_key(item: NewsItem) -> tuple[int, float]:
    if not item.published_at:
        return (0, 0.0)
    return (1, item.published_at.timestamp())


def assign_ids(section: str, items: list[NewsItem]) -> list[NewsItem]:
    prefix = "I" if section == "india" else "W"
    for index, item in enumerate(items, 1):
        item.item_id = f"{prefix}{index}"
    return items


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    result: list[NewsItem] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    for item in items:
        url_key = normalized_url(item.url)
        title_key = title_fingerprint(item.title)
        if url_key in seen_urls or title_key in seen_keys:
            continue
        seen_urls.add(url_key)
        seen_keys.add(title_key)
        result.append(item)
    return result


def normalized_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def title_fingerprint(title: str) -> str:
    words = keyword_set(title)
    return " ".join(sorted(words))


def keyword_set(text: str) -> set[str]:
    normalized = normalize_match_text(text)
    words = re.findall(r"[\w-]{4,}", normalized, flags=re.UNICODE)
    stopwords = {
        "about", "after", "from", "have", "into", "that", "their", "this", "with",
        "news", "latest", "today", "google", "india", "indian", "national",
        "politics", "political", "says", "said", "could", "first",
        "new", "update", "updates", "story", "stories", "live",
    }
    return {word for word in words if word not in stopwords}


def normalize_match_text(text: str) -> str:
    text = clean_text(text).lower()
    replacements = {
        r"\bpm\b": "prime minister",
        r"\bec\b": "election commission",
        r"\beci\b": "election commission of india",
        r"\bsc\b": "supreme court",
        r"\bls\b": "lok sabha",
        r"\brs\b": "rajya sabha",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    return text


def similar_titles(a: str, b: str) -> bool:
    left = keyword_set(a)
    right = keyword_set(b)
    if not left or not right:
        return False
    return len(left & right) / max(len(left), len(right)) >= 0.72


def related_titles(a: str, b: str) -> bool:
    left = keyword_set(a)
    right = keyword_set(b)
    if not left or not right:
        return False
    overlap = len(left & right)
    return overlap >= 3 and overlap / min(len(left), len(right)) >= 0.55


def group_related_items(items: list[NewsItem]) -> list[list[NewsItem]]:
    groups: list[list[NewsItem]] = []
    for item in items:
        matched_group = None
        for group in groups:
            if any(related_titles(item.title, existing.title) for existing in group):
                matched_group = group
                break
        if matched_group is None:
            groups.append([item])
        else:
            matched_group.append(item)
    return groups


def select_top_story_groups(section: str, items: list[NewsItem], settings: dict) -> list[list[NewsItem]]:
    groups = group_related_items(items)
    scored = []
    for group in groups:
        score, _ = score_story_group(group, settings)
        newest = max((item.published_at.timestamp() for item in group if item.published_at), default=0.0)
        scored.append((score, newest, group))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    max_groups = int(settings.get("max_groups_per_section", 8))
    min_score = int(settings.get("min_group_score", 2))
    selected = [group for score, _, group in scored if score >= min_score][:max_groups]
    if not selected:
        selected = [group for _, _, group in scored[:max_groups]]
    if not selected:
        print(f"Warning: no selected {section} story groups after scoring.", file=sys.stderr)
    return selected


def score_story_group(group: list[NewsItem], settings: dict) -> tuple[int, list[str]]:
    if not group:
        return 0, []

    section = group[0].section
    text = " ".join(f"{item.title} {item.source}" for item in group).lower()
    score = 0
    reasons: list[str] = []

    source_boost = min(4, max((max(0, item.source_weight) for item in group), default=1))
    score += source_boost
    reasons.append(f"source weight +{source_boost}")

    if section == "india":
        india_hits = keyword_hits(text, section_keywords("india", settings))
        if india_hits:
            boost = min(6, india_hits * 2)
            score += boost
            reasons.append(f"national politics +{boost}")
        else:
            score -= 2
            reasons.append("weak India match -2")
    else:
        world_hits = keyword_hits(text, section_keywords("world", settings))
        if world_hits:
            boost = min(5, world_hits)
            score += boost
            reasons.append(f"policy politics +{boost}")

    public_hits = keyword_hits(
        text,
        configured_keywords(
            settings,
            "research_keywords",
            (
                "parliament,lok sabha,rajya sabha,bill,ordinance,cabinet,election,"
                "election commission,supreme court,constitution,governance,policy,alliance,party"
            ),
        ),
    )
    if public_hits:
        boost = min(6, public_hits * 2)
        score += boost
        reasons.append(f"political significance +{boost}")

    classroom_hits = keyword_hits(
        text,
        configured_keywords(
            settings,
            "classroom_keywords",
            "parliament,constitution,election,policy,bill,supreme court,governance,federalism,rights,welfare",
        ),
    )
    if classroom_hits:
        boost = min(3, classroom_hits)
        score += boost
        reasons.append(f"civic value +{boost}")

    if len(group) > 1:
        boost = min(3, len(group) - 1)
        score += boost
        reasons.append(f"related headlines +{boost}")

    if len(unique_sources(group)) > 1:
        score += 2
        reasons.append("multiple sources +2")

    recency_boost = recency_score(group)
    if recency_boost:
        score += recency_boost
        reasons.append(f"freshness +{recency_boost}")

    low_value_hits = keyword_hits(
        text,
        configured_keywords(
            settings,
            "low_value_keywords",
            "campus diary,opinion,editorial,celebrity,entertainment,promotion,launch offer,poster,trailer",
        ),
    )
    if low_value_hits:
        penalty = min(6, low_value_hits * 3)
        score -= penalty
        reasons.append(f"low value -{penalty}")

    if len(keyword_set(text)) <= 2:
        score -= 2
        reasons.append("vague headline -2")

    return score, reasons


def keyword_hits(text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if keyword and keyword in text)


def unique_sources(group: list[NewsItem]) -> set[str]:
    return {clean_text(item.source).lower() for item in group if clean_text(item.source)}


def recency_score(group: list[NewsItem]) -> int:
    newest = max((item.published_at for item in group if item.published_at), default=None)
    if not newest:
        return 0
    age = datetime.now(timezone.utc) - newest.astimezone(timezone.utc)
    if age <= timedelta(hours=6):
        return 2
    if age <= timedelta(hours=12):
        return 1
    return 0


def prompt_story_groups(items: list[NewsItem], settings: dict) -> str:
    lines: list[str] = []
    for section, heading in (("india", "India National Politics"), ("world", "Policy and Institutions")):
        lines.append(f"{heading} candidate story groups:")
        section_items = [item for item in items if item.section == section]
        if not section_items:
            lines.append("- No fresh items found for this section.")
            continue
        for group_index, group in enumerate(group_related_items(section_items), 1):
            ids = ", ".join(f"[{item.item_id}]" for item in group)
            dates = ", ".join(sorted({item.published for item in group if item.published}))
            score, reasons = score_story_group(group, settings)
            signals = "; ".join(reasons[:4])
            lines.append(f"- Group {group_index} {ids}; score: {score}; signals: {signals}; dates: {dates}")
            for item in group:
                lines.append(f"  {item.item_id}. {item.title} | {item.source}")
    return "\n".join(lines)


def load_quota() -> dict:
    if not QUOTA_FILE.exists():
        return {"day": "", "count": 0, "last_call": 0.0}
    try:
        return json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"day": "", "count": 0, "last_call": 0.0}


def reserve_gemini_call(max_daily_calls: int, min_interval_seconds: int) -> None:
    DATA.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    quota = load_quota()
    if quota.get("day") != today:
        quota = {"day": today, "count": 0, "last_call": 0.0}
    if int(quota.get("count", 0)) >= max_daily_calls:
        raise RuntimeError(f"Daily Gemini call limit reached: {max_daily_calls}")
    elapsed = time.time() - float(quota.get("last_call", 0.0))
    if elapsed < min_interval_seconds:
        time.sleep(min_interval_seconds - elapsed)
    quota["count"] = int(quota.get("count", 0)) + 1
    quota["last_call"] = time.time()
    QUOTA_FILE.write_text(json.dumps(quota, indent=2), encoding="utf-8")


def gemini_summary(items: list[NewsItem], api_key: str, points_total: int, settings: dict) -> str:
    reserve_gemini_call(max_daily_calls=20, min_interval_seconds=12)
    model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    current_date = datetime.now(IST).date().isoformat()
    max_points = min(5, points_total)
    prompt_items = prompt_story_groups(items, settings)
    prompt = f"""
Create an English daily national political news brief for India.

Current IST date: {current_date}

Rules:
- First line must be: TITLE: concise title for the full brief.
- Second line must be: SUMMARY: one concise homepage line covering the main themes across both sections.
- The SUMMARY must not copy the first bullet. It should combine 2 to 4 themes, for example: "Parliament, cabinet decisions, election updates, and court-policy issues."
- Keep SUMMARY under 160 characters.
- Then produce exactly two sections:
  SECTION: India National Politics
  SECTION: Policy and Institutions
- Across both sections combined, output 0 to {max_points} significant bullet points total.
- It is better to output fewer than {max_points} points than to include weak, duplicate, stale, or filler news.
- Never exceed {max_points} bullet points total.
- When both sections have strong candidates, include at least one item from each section. Do not force a weak item.
- Use only the supplied items. They were pre-filtered for today's IST date.
- Only the highest-scored candidate groups are shown; do not ask for or infer omitted stories.
- Prioritize national-level Indian politics only: Parliament, Union government, cabinet, elections, national parties, constitutional issues, and Supreme Court matters with political impact.
- Keep routine speeches, rallies, allegations, or party reactions only if they affect national policy, institutions, elections, or governance.
- Do not include state-only political drama unless it has clear national significance.
- Treat each candidate group as one possible story. If a group has multiple headlines, synthesize them into one coherent point.
- Merge repeated or similar headlines into one bullet and cite all relevant source ids from that group.
- Do not merge unrelated bills, court cases, election issues, speeches, or party statements just because they share a broad theme.
- Every source id at the end of a bullet must directly support that bullet's specific claim. Do not cite a source if it only shares a broad topic.
- If a bullet combines two related claims, include only the source ids that support those exact claims.
- Do not create separate bullets for small variants of the same story.
- Keep every point factual and grounded in the supplied headlines only.
- Prefer concrete national political developments over gossip, speculation, routine reactions, or personality coverage.
- End each bullet with source ids using this exact format: Sources: [I1], [I3] or Sources: [W2]
- Do not include inline URLs.
- Format bullets as: - **Short topic:** one concise synthesized sentence explaining what happened and why it matters nationally. Sources: [I1], [W3]

Candidate story groups:
{prompt_items}
""".strip()
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1100},
    }
    body = json.dumps(payload).encode("utf-8")
    url = f"{GEMINI_API_ROOT}/{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {data}") from exc


def fallback_summary(items: list[NewsItem], points_total: int, settings: dict) -> str:
    lines = [
        "TITLE: Political News Brief",
        f"SUMMARY: {fallback_home_summary(items, points_total, settings)}",
    ]
    max_points = min(5, points_total)
    selected_by_section = {"india": [], "world": []}
    scored_groups = []
    for section in ("india", "world"):
        section_items = [item for item in items if item.section == section]
        for group in group_related_items(section_items):
            score, _ = score_story_group(group, settings)
            newest = max((item.published_at.timestamp() for item in group if item.published_at), default=0.0)
            scored_groups.append((score, newest, section, group))
    scored_groups.sort(key=lambda row: (row[0], row[1]), reverse=True)

    min_score = int(settings.get("min_group_score", 2))
    selected_keys: set[tuple[str, ...]] = set()

    for section in ("india", "world"):
        section_candidates = [row for row in scored_groups if row[2] == section and row[0] >= min_score]
        if section_candidates and sum(len(groups) for groups in selected_by_section.values()) < max_points:
            _, _, _, group = section_candidates[0]
            selected_by_section[section].append(group)
            selected_keys.add(tuple(item.item_id for item in group))

    remaining = max_points - sum(len(groups) for groups in selected_by_section.values())
    for _, _, section, group in scored_groups:
        if remaining <= 0:
            break
        key = tuple(item.item_id for item in group)
        if key in selected_keys:
            continue
        selected_by_section[section].append(group)
        selected_keys.add(key)
        remaining -= 1

    for section, heading in (("india", "India National Politics"), ("world", "Policy and Institutions")):
        lines.append(f"SECTION: {heading}")
        for group in selected_by_section[section]:
            lead = group[0]
            source_ids = ", ".join(f"[{item.item_id}]" for item in group[:4])
            lines.append(f"- **{readable_title(lead.source)}:** {readable_title(lead.title)} Sources: {source_ids}")
    return "\n".join(lines)


def fallback_home_summary(items: list[NewsItem], points_total: int, settings: dict) -> str:
    topics: list[str] = []
    max_points = min(5, points_total)
    for section in ("india", "world"):
        section_items = [item for item in items if item.section == section]
        groups = group_related_items(section_items)
        groups.sort(key=lambda group: score_story_group(group, settings)[0], reverse=True)
        for group in groups[:max_points]:
            topic = story_topic(group)
            if topic and topic.lower() not in {existing.lower() for existing in topics}:
                topics.append(topic)
            if len(topics) >= 4:
                return join_summary_topics(topics)
    return join_summary_topics(topics) if topics else "Daily national political news from India"


def story_topic(group: list[NewsItem]) -> str:
    text = " ".join(headline_without_source(item) for item in group).lower()
    topic_rules = [
        ("parliament", ("parliament", "lok sabha", "rajya sabha", "bill", "ordinance")),
        ("union government", ("cabinet", "prime minister", "minister", "central government", "union government")),
        ("elections", ("election", "election commission", "eci", "voter", "poll")),
        ("constitutional issues", ("constitution", "supreme court", "rights", "federalism")),
        ("party politics", ("bjp", "congress", "nda", "india bloc", "alliance", "opposition")),
        ("governance policy", ("policy", "welfare", "scheme", "governance", "committee")),
    ]
    for label, keywords in topic_rules:
        if any(keyword in text for keyword in keywords):
            return label
    return ""


def headline_without_source(item: NewsItem) -> str:
    title = clean_text(item.title)
    source = clean_text(item.source)
    if source:
        title = re.sub(rf"\s+-\s*{re.escape(source)}$", "", title, flags=re.I)
    return title


def join_summary_topics(topics: list[str]) -> str:
    clean_topics = [topic for topic in topics if topic]
    if not clean_topics:
        return "Daily national political news from India"
    if len(clean_topics) == 1:
        return f"{clean_topics[0].capitalize()} in Indian national politics"
    if len(clean_topics) == 2:
        return f"{clean_topics[0].capitalize()} and {clean_topics[1]} in Indian national politics"
    return f"{', '.join(clean_topics[:-1]).capitalize()}, and {clean_topics[-1]} in Indian national politics"


def plain_text(markdown: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown)
    text = re.sub(r"[*_`#>~]+", "", text)
    text = re.sub(r"\[[A-Z]\d+\]", "", text)
    return clean_text(text)


def readable_title(text: str) -> str:
    text = clean_text(text)
    letters = [char for char in text if char.isalpha()]
    if letters and sum(char.isupper() for char in letters) / len(letters) > 0.82:
        small_words = {"a", "an", "and", "as", "at", "for", "from", "in", "of", "on", "or", "the", "to"}
        words = text.lower().split()
        titled = []
        for index, word in enumerate(words):
            titled.append(word if index > 0 and word in small_words else word[:1].upper() + word[1:])
        return " ".join(titled)
    return text


def split_digest_header(summary: str) -> tuple[str, str, str]:
    lines = summary.splitlines()
    remaining: list[str] = []
    title = ""
    teaser = ""
    for line in lines:
        match = re.match(r"^TITLE\s*:\s*(.+)$", line.strip(), flags=re.I)
        if match and not title:
            title = clean_title(match.group(1))
            continue
        summary_match = re.match(r"^SUMMARY\s*:\s*(.+)$", line.strip(), flags=re.I)
        if summary_match and not teaser:
            teaser = clean_summary(summary_match.group(1))
            continue
        remaining.append(line)
    return title or "Political News Brief", teaser, "\n".join(remaining).strip()


def split_digest_title(summary: str) -> tuple[str, str]:
    title, _, body = split_digest_header(summary)
    return title, body


def clean_title(value: str) -> str:
    title = plain_text(value).strip(" .,:;-")
    return title[:80].rstrip(" ,;:") if title else "Political Brief"


def clean_summary(value: str) -> str:
    summary = plain_text(value).strip(" .,:;-")
    return summary[:157].rstrip() + "..." if len(summary) > 160 else summary


def generic_title(title: str) -> bool:
    normalized = clean_text(title).lower()
    generic_titles = {
        "political brief",
        "political news brief",
        "daily political news brief",
        "india and world political brief",
    }
    return normalized in generic_titles


def yaml_escape(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def item_map(items: list[NewsItem]) -> dict[str, NewsItem]:
    return {item.item_id: item for item in items}


def extract_source_ids(text: str) -> tuple[str, list[str]]:
    source_ids = [match.upper() for match in re.findall(r"\[([IW]\d+)\]", text, flags=re.I)]
    text = re.sub(r"\s*Sources?:\s*(?:\[[IW]\d+\]\s*,?\s*)+$", "", text, flags=re.I)
    text = re.sub(r"\s*(?:\[[IW]\d+\]\s*)+$", "", text, flags=re.I)
    return clean_text(text), source_ids


def infer_source_ids(text: str, items: list[NewsItem], section: str, limit: int = 2) -> list[str]:
    text_words = keyword_set(plain_text(text))
    if not text_words:
        return []
    scored = []
    for item in items:
        if item.section != section:
            continue
        overlap = len(text_words & keyword_set(item.title))
        if overlap:
            scored.append((overlap, item.item_id))
    scored.sort(reverse=True)
    return [item_id for _, item_id in scored[:limit]]


def source_chips_html(source_ids: list[str], lookup: dict[str, NewsItem]) -> str:
    links = []
    seen = set()
    for source_id in source_ids:
        if source_id in seen or source_id not in lookup:
            continue
        seen.add(source_id)
        item = lookup[source_id]
        label = html.escape(source_id)
        url = html.escape(item.url, quote=True)
        links.append(f'<a href="{url}">{label}</a>')
    return f'<span class="source-chips">{" ".join(links)}</span>' if links else ""


def validate_source_ids(text: str, source_ids: list[str], lookup: dict[str, NewsItem], section: str) -> list[str]:
    valid: list[str] = []
    for source_id in source_ids:
        item = lookup.get(source_id)
        if not item or item.section != section:
            continue
        if source_relevance_score(text, item) >= 2:
            valid.append(source_id)
    return valid


def source_relevance_score(text: str, item: NewsItem) -> int:
    bullet_words = keyword_set(plain_text(text))
    title_words = keyword_set(item.title)
    overlap = len(bullet_words & title_words)
    score = overlap

    bullet_text = normalize_match_text(text)
    title_text = normalize_match_text(item.title)
    phrase_pairs = (
        ("parliament", "parliament"),
        ("lok sabha", "lok sabha"),
        ("rajya sabha", "rajya sabha"),
        ("cabinet", "cabinet"),
        ("prime minister", "prime minister"),
        ("election commission", "election commission"),
        ("supreme court", "supreme court"),
        ("constitution", "constitution"),
        ("bill", "bill"),
        ("ordinance", "ordinance"),
        ("bjp", "bjp"),
        ("congress", "congress"),
        ("nda", "nda"),
        ("india bloc", "india bloc"),
        ("policy", "policy"),
    )
    for bullet_phrase, title_phrase in phrase_pairs:
        if bullet_phrase in bullet_text and title_phrase in title_text:
            score += 2

    return score


def inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)


def summary_to_html(summary: str, items: list[NewsItem], points_per_section: int = 5) -> str:
    _, body = split_digest_title(summary)
    lookup = item_map(items)
    current_section = ""
    total_count = 0
    max_points = min(5, points_per_section)
    html_lines: list[str] = []
    in_list = False

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section_match = re.match(r"^SECTION\s*:\s*(.+)$", line, flags=re.I)
        if section_match:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            heading = clean_title(section_match.group(1))
            current_section = "india" if "india" in heading.lower() else "world"
            html_lines.append(f'<h2 class="section-title">{html.escape(heading)}</h2>')
            html_lines.append('<ul class="digest-points">')
            in_list = True
            continue
        if line.startswith(("- ", "* ")):
            if total_count >= max_points:
                continue
            if not in_list:
                html_lines.append('<ul class="digest-points">')
                in_list = True
            text, source_ids = extract_source_ids(line[2:].strip())
            source_ids = validate_source_ids(text, source_ids, lookup, current_section)
            if not source_ids:
                source_ids = infer_source_ids(text, items, current_section)
            html_lines.append(f"  <li>{inline_markdown_to_html(text)}{source_chips_html(source_ids, lookup)}</li>")
            total_count += 1

    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def sources_to_html(items: list[NewsItem]) -> str:
    lines = ['<ul class="source-list">']
    for item in items:
        title = html.escape(readable_title(item.title))
        url = html.escape(item.url, quote=True)
        source = html.escape(item.source)
        lines.append(f'  <li><a href="{url}">[{item.item_id}] {title}</a><span>{source}</span></li>')
    lines.append("</ul>")
    return "\n".join(lines)


def summary_line_text(text: str, items: list[NewsItem]) -> str:
    source_names = sorted({clean_text(item.source) for item in items if clean_text(item.source)}, key=len, reverse=True)
    for source in source_names:
        text = re.sub(rf"^\*\*{re.escape(source)}:?\*\*\s*:?\s*", "", text, flags=re.I)
    text = plain_text(text)
    for source in source_names:
        text = re.sub(rf"\s+-?\s*{re.escape(source)}$", "", text, flags=re.I).strip()
    return clean_text(text)


def one_line_summary(summary: str, items: list[NewsItem]) -> str:
    _, teaser, _ = split_digest_header(summary)
    if teaser:
        return teaser[:157].rstrip() + "..." if len(teaser) > 160 else teaser
    _, body = split_digest_title(summary)
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")):
            text, _ = extract_source_ids(line[2:].strip())
            text = summary_line_text(text, items)
            return text[:157].rstrip() + "..." if len(text) > 160 else text
    return "Daily national political news from India"


def build_post(summary: str, items: list[NewsItem], used_ai: bool, points_per_section: int) -> Path:
    POSTS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)
    post_path = POSTS / f"{now_ist.date().isoformat()}-political-brief.md"
    title, _ = split_digest_title(summary)
    teaser = one_line_summary(summary, items)
    if generic_title(title):
        title = clean_title(teaser)
    run_time = now_ist.strftime("%-I:%M%p")
    ai_note = f"Gemini Summary: {run_time}" if used_ai else f"Headline Digest: {run_time}"
    content = f"""---
layout: default
title: {yaml_escape(title)}
date: {now.isoformat()}
summary: {yaml_escape(teaser)}
run_time_ist: {yaml_escape(run_time)}
---

<article class="digest-post">
  <a class="back-link" href="{{{{ '/' | relative_url }}}}">{SITE_TITLE}</a>
  <p class="post-meta">{ai_note}</p>

{summary_to_html(summary, items, points_per_section)}

<details class="tp-sources">
<summary>Sources considered</summary>

{sources_to_html(items)}

</details>
</article>
"""
    post_path.write_text(content, encoding="utf-8")
    return post_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the daily political news brief.")
    parser.add_argument("--config", default=SOURCE_CONFIG, help="Path to source YAML config.")
    parser.add_argument("--no-ai", action="store_true", help="Skip Gemini and write headline bullets.")
    args = parser.parse_args()

    read_env_file()
    config = parse_simple_yaml(ROOT / args.config)
    items = collect_news(config)
    if not items:
        print("No news items found.", file=sys.stderr)
        return 1

    points = min(5, int(config.get("settings", {}).get("final_points_total", 5)))
    api_key = os.environ.get("POLITICAL_API_KEY")
    used_ai = bool(api_key and not args.no_ai)
    try:
        summary = gemini_summary(items, api_key, points, config.get("settings", {})) if used_ai else fallback_summary(items, points, config.get("settings", {}))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Gemini request failed: HTTP {exc.code}: {detail}", file=sys.stderr)
        summary = fallback_summary(items, points, config.get("settings", {}))
        used_ai = False
    except Exception as exc:
        print(f"Gemini summary failed: {exc}", file=sys.stderr)
        summary = fallback_summary(items, points, config.get("settings", {}))
        used_ai = False

    post_path = build_post(summary, items, used_ai, points)
    print(f"Wrote {post_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
