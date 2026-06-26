# Political Brief

Daily English national political brief for India.

The project collects configurable RSS feeds, filters for fresh national-level political items, groups duplicates, scores stories before the AI call, asks Gemini for a concise digest, and writes a Jekyll Markdown post under `docs/_posts/`.

## Output

- `India National Politics`: Parliament, Union government, cabinet, elections, parties, alliances
- `Policy and Institutions`: Supreme Court, constitutional issues, Election Commission, national governance
- The final post lists at most five points total. If there are not enough worthwhile stories, it shows fewer.
- Each point keeps source chips that link back to the supporting article/feed item.

## Local Run

Create `.env` locally:

```bash
POLITICAL_API_KEY=your_gemini_key_here
GEMINI_MODEL=gemini-3.1-flash-lite
```

Generate:

```bash
./run.sh generate
```

Run without Gemini:

```bash
./run.sh no-ai
```

Preview locally:

```bash
./run.sh serve
```

## Sources

Edit:

```text
config/sources.yml
```

Add a source under `india` or `world`:

```yml
- name: Example Political Source
  type: rss
  weight: 3
  url: "https://example.com/rss.xml"
```

Before Gemini runs, the script filters old and irrelevant items, removes excluded topics, groups similar headlines, scores each group, and sends only the top `max_groups_per_section` groups per section.

## GitHub Deployment

1. Push this folder as the root of a repo named `political-news`.
2. Add a GitHub Actions repository secret:

```text
POLITICAL_API_KEY
```

3. In GitHub Pages settings, set source to `GitHub Actions`.

The site is configured for:

```text
/political-news
```

## Schedule

The workflow runs at:

- 06:00 IST
- 14:00 IST
- 20:00 IST

Each successful run commits the generated post into `docs/_posts/` and deploys GitHub Pages.
