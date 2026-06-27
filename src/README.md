# Political News `src` Split

This `src` folder is a split version of the original single-file generator.

## Files

- `main.py` — command-line entry point and orchestration.
- `common.py` — shared constants, paths, `NewsItem`, environment loading, and small YAML parser.
- `fetch.py` — RSS/Atom fetching and conversion into `NewsItem` objects.
- `directlink.py` — direct-link resolver.
- `filter.py` — freshness, relevance, deduplication, ranking, grouping, and source validation.
- `ai.py` — Gemini prompt, API call, quota handling, and fallback summary.
- `markdown.py` — Markdown/front matter and HTML post generation.

## Direct-link behavior

The source link policy is strict:

1. `fetch.py` reads the RSS/Atom entry link.
2. `fetch.py` calls `resolve_direct_link()` from `directlink.py`.
3. `directlink.py` opens the indirect link with a browser-like GET request.
4. It returns only the final URL reached by HTTP redirect handling.
5. If the link cannot be resolved, the item is skipped.

This avoids RSS links, Google image/static links, favicon links, and guessed page-body links.

## Important

Old generated posts keep old links until regenerated. Run the workflow again to regenerate today’s post.
