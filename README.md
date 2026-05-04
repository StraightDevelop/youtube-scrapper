# youtube-channel-scrapper

Monorepo for personal YouTube transcript scraping tools.

## Layout

```
apps/
  youtube_scraper/       ← CLI: scrape every public video on a channel → JSONL
  youtube_scraper_web/   ← Streamlit UI: paste channel or video URL → download JSONL
shared/                  ← reusable services (yt-dlp wrapper, transcript fetch, IO, utils)
output/                  ← generated .jsonl + .summary.json (gitignored)
```

The `apps/` ↔ `shared/` split is enforced by [CLAUDE.md](./CLAUDE.md):
apps may import from `shared/`, but `shared/` must remain framework- and
project-agnostic.

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# CLI mode
python apps/youtube_scraper/scrape.py https://youtube.com/@SomeChannel

# Web UI (paste box, downloads, runs locally)
streamlit run apps/youtube_scraper_web/app.py
```

Full usage, schema, and troubleshooting:

- CLI: [`apps/youtube_scraper/README.md`](./apps/youtube_scraper/README.md)
- Web UI: [`apps/youtube_scraper_web/README.md`](./apps/youtube_scraper_web/README.md)

## Project meta files

- [`CLAUDE.md`](./CLAUDE.md) — agent rules and protocols (DDD, FURPS+, snake_case, etc.)
- [`PROJECT_INFO.md`](./PROJECT_INFO.md) — file structure index (kept in sync with code)
- [`CHANGELOG.md`](./CHANGELOG.md) — versioned change log
- [`TODOS.md`](./TODOS.md) — working checklist
# youtube-scrapper
