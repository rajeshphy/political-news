from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone

try:
    from .common import IST, NewsItem, clean_text, config_bool
except ImportError:
    from common import IST, NewsItem, clean_text, config_bool


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

    relevant: list[NewsItem] = []

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

    useful: list[NewsItem] = []

    for item in items:
        haystack = f"{item.title} {item.source}".lower()

        if not any(keyword in haystack for keyword in excluded):
            useful.append(item)

    return useful


def section_keywords(section: str, settings: dict) -> list[str]:
    default_india = "india,indian,national,central government,parliament,lok sabha,rajya sabha,cabinet,election commission"
    default_world = "policy,bill,ordinance,parliament,cabinet,election,alliance,party,governance,constitution,supreme court"
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
        "about",
        "after",
        "from",
        "have",
        "into",
        "that",
        "their",
        "this",
        "with",
        "news",
        "latest",
        "today",
        "google",
        "india",
        "indian",
        "national",
        "politics",
        "political",
        "says",
        "said",
        "could",
        "first",
        "new",
        "update",
        "updates",
        "story",
        "stories",
        "live",
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


def source_relevance_score(text: str, item: NewsItem) -> int:
    bullet_words = keyword_set(plain_text_for_filter(text))
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


def plain_text_for_filter(markdown: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown)
    text = re.sub(r"[*_`#>~]+", "", text)
    text = re.sub(r"\[[A-Z]\d+\]", "", text)
    return clean_text(text)
