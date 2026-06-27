from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
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


def parse_yaml_value(value: str):
    value = value.strip()

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]

    if value.isdigit():
        return int(value)

    return value


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

        if section == "sources" and line.startswith(" ") and stripped.endswith(":"):
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


def config_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
