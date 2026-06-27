#!/usr/bin/env python3
"""Generate an English daily political news brief."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error

try:
    from .ai import fallback_summary, gemini_summary
    from .common import ROOT, SOURCE_CONFIG, parse_simple_yaml, read_env_file
    from .fetch import collect_news
    from .markdown import build_post
except ImportError:
    from ai import fallback_summary, gemini_summary
    from common import ROOT, SOURCE_CONFIG, parse_simple_yaml, read_env_file
    from fetch import collect_news
    from markdown import build_post


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
    api_key = os.environ.get("POLITICAL_API_KEY") or os.environ.get("GEMINI_API_KEY")
    used_ai = bool(api_key and not args.no_ai)

    try:
        summary = (
            gemini_summary(items, api_key, points, config.get("settings", {}))
            if used_ai
            else fallback_summary(items, points, config.get("settings", {}))
        )

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
