from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from .common import IST, POSTS, SITE_TITLE, NewsItem, clean_text
    from .filter import keyword_set, source_relevance_score
except ImportError:
    from common import IST, POSTS, SITE_TITLE, NewsItem, clean_text
    from filter import keyword_set, source_relevance_score


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


def headline_without_source(item: NewsItem) -> str:
    title = clean_text(item.title)
    source = clean_text(item.source)

    if source:
        title = re.sub(rf"\s+-\s*{re.escape(source)}$", "", title, flags=re.I)

    return title


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
    links: list[str] = []
    seen: set[str] = set()

    for source_id in source_ids:
        if source_id in seen or source_id not in lookup:
            continue

        seen.add(source_id)
        item = lookup[source_id]
        label = html.escape(source_id)
        url = html.escape(item.url, quote=True)
        links.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>')

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
            html_lines.append(f'\n<h2 class="section-title">{html.escape(heading)}</h2>\n')
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
        lines.append(
            f'  <li><a href="{url}" target="_blank" rel="noopener noreferrer">[{item.item_id}] {title}</a> {source}</li>'
        )

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

    try:
        run_time = now_ist.strftime("%-I:%M%p")
    except ValueError:
        run_time = now_ist.strftime("%I:%M%p").lstrip("0")

    ai_note = f"Gemini Summary: {run_time}" if used_ai else f"Headline Digest: {run_time}"

    content = f"""---
layout: default
title: {yaml_escape(title)}
date: {now.isoformat()}
summary: {yaml_escape(teaser)}
run_time_ist: {yaml_escape(run_time)}
---

<article class="digest-post">
  <a class="back-link" href="{{{{ '/' | relative_url }}}}">{html.escape(SITE_TITLE)}</a>
  <p class="post-meta">{html.escape(ai_note)}</p>

{summary_to_html(summary, items, points_per_section)}

<details class="tp-sources">
<summary>Sources considered</summary>

{sources_to_html(items)}

</details>
</article>
"""

    post_path.write_text(content, encoding="utf-8")
    return post_path
