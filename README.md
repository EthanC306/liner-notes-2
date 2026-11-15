# Liner Notes

A Spotify listening-stats site that reads like a music autobiography. Upload your Spotify
data export and get your **eras**, the artists you **ghosted**, songs you **skip but never
remove**, **dead weight** in your playlists, **count vs. love**, decades, genres, a listening
clock, friend **compatibility**, a **local scene** map, and recommendations.

See [PLAN.md](PLAN.md) for the design decisions and risks.

## Run it

### With Docker (recommended)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine) and git.

```bash
git clone https://github.com/EthanC306/liner-notes-2.git
cd liner-notes-2
docker compose up --build
```

Open **http://localhost:8000** and click **Try the demo listener**. Demo data is built into the
image, so this works with no accounts or API keys. Stop it with `Ctrl+C`; your data is kept in a
Docker volume (`docker compose down -v` wipes it).

Prefer not to clone? Run a released image directly:

```bash
docker run -p 8000:8000 ghcr.io/ethanc306/liner-notes-2:latest
```

To turn on optional features (Last.fm, Spotify login, Ollama), copy `.env.example` to `.env`,
fill in what you need, and restart. See [Configuration](#configuration-environment-variables).

### Without Docker (for development)

Needs Python 3.12+.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/make_sample_export.py --seed-db   # you
.venv/bin/python scripts/make_sample_export.py --friend    # someone to compare with
cd backend && ../.venv/bin/uvicorn main:app --reload
```

Or the short version: `make setup` once, then `make` (also `make test`, `make demo`).

## Using real data

1. Request your export at spotify.com → Account → Privacy settings ("Account data" and/or
   "Extended streaming history").
2. Upload the zip on the landing page. Keep the **profile key** it gives you: it's your login,
   and you use it to merge more data later or to compare with a friend.

This gets around Spotify's 5-user Development Mode cap, since no Spotify login is involved.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite file in `backend/` | e.g. `postgresql+psycopg://liner:liner@localhost:5432/linernotes` (`docker compose --profile postgres up` starts one; use host `db` instead of `localhost` from inside Docker) |
| `LASTFM_API_KEY` | none | Genres, similar artists, top tracks ([get a free key](https://www.last.fm/api/account/create)) |
| `OLLAMA_URL` | `http://localhost:11434` | Optional local LLM |
| `OLLAMA_MODEL` | first installed model | e.g. `llama3.2` |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | none | Quick setup ("Connect Spotify"). From the [Spotify developer dashboard](https://developer.spotify.com/dashboard). Development Mode allows 5 users, each added in the dashboard, and the app owner needs Premium |
| `SPOTIFY_REDIRECT_URI` | `<site>/api/spotify/callback` | Must exactly match a redirect URI registered in the dashboard. Spotify rejects `localhost`, so locally open the site at `http://127.0.0.1:8000` and register `http://127.0.0.1:8000/api/spotify/callback` |
| `SPOTIFY_FAKE` | off | `1` makes "Connect Spotify" skip the real login and use built-in sample data, to try quick setup without keys (`make run-fake-spotify`) |
| `PUBLIC_URL` | the request's address | Base of password-reset links (printed to the server log until email is set up) |

Without Last.fm, real uploads won't have genres, recommendations, or the genre half of eras (the demo
has its metadata seeded). Without Ollama, the summary uses a template and recommendations show
similarity reasons instead of AI-written explanations.

## Releases

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the tests and builds and smoke-tests
the Docker image on every push to `main` and every pull request.

To publish a version, tag it and push the tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

[`release.yml`](.github/workflows/release.yml) then runs the tests, pushes the image to
`ghcr.io/ethanc306/liner-notes-2` as `1.0.0`, `1.0` and `latest`, and creates a GitHub Release with
generated notes. Use [semantic versioning](https://semver.org): bump the patch for fixes, the minor
for new features.

## Layout

```
backend/   main.py (API routes)  stats.py (SQL stats)  parser.py (export parsing)
           enrich.py (ALL external APIs)  recommend.py (recs + AI)  db.py (models)
frontend/  index.html  styles.css  app.js (vanilla JS + Chart.js)
scripts/   make_sample_export.py
tests/     test_app.py  (run: .venv/bin/pytest)
Dockerfile, docker-compose.yml, docker-entrypoint.sh, .env.example
.github/workflows/  ci.yml  release.yml
```

## Rediscovery Mixtape

The **Mixtape** tab builds a tape of songs you loved but stopped playing, ordered as a walk through your
eras with a liner note for each one. Use the sliders to set the length and how long a song has to be gone. **Save**
gives you a public link (`/?mixtape=...`) that shows only the tracklist. **Copy to Spotify** works like the
fix-it exports below.

## Fix-it actions

The Clean up, Ghosts, and Skips pages export `spotify:track:` URIs and copy them to your
clipboard. In the Spotify desktop app, open a playlist and press Ctrl+V (Cmd+V on a Mac) to add
them all, or use the list to find and remove dead weight. No Spotify API access needed.
