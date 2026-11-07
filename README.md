# Liner Notes

A Spotify listening-stats site that reads like a music autobiography. Upload your Spotify
data export and get your **eras**, the artists you **ghosted**, songs you **skip but never
remove**, **dead weight** in your playlists, **count vs. love**, decades, genres, a listening
clock, friend **compatibility**, a **local scene** map, and recommendations.

See [PLAN.md](PLAN.md) for the design decisions and risks.

## Quick start (demo data, no Spotify needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/make_sample_export.py --seed-db   # you
.venv/bin/python scripts/make_sample_export.py --friend    # someone to compare with
cd backend && ../.venv/bin/uvicorn main:app --reload
```

Open http://localhost:8000 and click **Try the demo listener**.

## Using real data

1. Request your export at spotify.com → Account → Privacy settings ("Account data" and/or
   "Extended streaming history").
2. Upload the zip on the landing page. Keep the **profile key** it gives you: it's your login,
   and you use it to merge more data later or to compare with a friend.

This gets around Spotify's 5-user Development Mode cap, since no Spotify login is involved.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite file in `backend/` | e.g. `postgresql+psycopg://liner:liner@localhost:5432/linernotes` (`docker compose up -d` starts one) |
| `LASTFM_API_KEY` | none | Genres, similar artists, top tracks ([get a free key](https://www.last.fm/api/account/create)) |
| `OLLAMA_URL` | `http://localhost:11434` | Optional local LLM |
| `OLLAMA_MODEL` | first installed model | e.g. `llama3.2` |

Without Last.fm, real uploads won't have genres, recommendations, or the genre half of eras (the demo
has its metadata seeded). Without Ollama, the summary uses a template and recommendations show
similarity reasons instead of AI-written explanations.

## Layout

```
backend/   main.py (API routes)  stats.py (SQL stats)  parser.py (export parsing)
           enrich.py (ALL external APIs)  recommend.py (recs + AI)  db.py (models)
frontend/  index.html  styles.css  app.js (vanilla JS + Chart.js)
scripts/   make_sample_export.py
tests/     test_app.py  (run: .venv/bin/pytest)
```

## Fix-it actions

The Clean up, Ghosts, and Skips pages export `spotify:track:` URIs and copy them to your
clipboard. In the Spotify desktop app, open a playlist and press Ctrl+V (Cmd+V on a Mac) to add
them all, or use the list to find and remove dead weight. No Spotify API access needed.
