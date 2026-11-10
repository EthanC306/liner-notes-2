# Liner Notes — Build Plan

A Spotify listening-stats site that reads like a music autobiography: eras, ghosts,
skips, dead weight, and fix-it actions, not just a scoreboard.

## Decisions (from discussion posts + critique)

| Area | Decision | Why |
|---|---|---|
| Data source | User uploads their **Spotify data export** (zip or JSON files) | Avoids the 5-user Development Mode cap entirely. No Spotify login needed. |
| Backend | **Python + FastAPI** | Handles uploads, serves stats as JSON, runs enrichment as a background task. |
| Database | **PostgreSQL** (SQLite fallback for quick local dev) | Every stat is an aggregation. Same SQLAlchemy code runs on both. |
| Frontend | **Vanilla HTML/CSS/JS + Chart.js** | Simple and enough for tabs plus filters. |
| Enrichment | Last.fm (genres, similar artists) + MusicBrainz (release year), all in `enrich.py`, cached per artist/track | One module to swap if an API changes. Each lookup runs once. |
| AI | **Ollama on the server, optional** | Only ranks and explains candidates from real Last.fm data, so it can't make up songs. Writes the autobiography summary. Everything works with it off. |
| Privacy | Raw uploads parsed in memory and **never written to disk**. One-click "delete everything". | Listening history is personal data. |

## Risks and how they're handled

- **Spotify API churn**: we don't depend on Spotify's API at all. External calls live in `enrich.py`.
- **Slow export delivery (days to weeks)**: accepts the fast "Account data" export (basic history) right away. The extended history adds skips and full years when it arrives, and re-uploading merges and dedupes.
- **Bad metadata**: missing years and genres show up as "Unknown" instead of being guessed.
- **LLM hallucination**: the LLM only reorders a fixed candidate list. Output is checked against that list.
- **GPU hosting**: Ollama is optional (`OLLAMA_URL`). Without it, recommendations use plain similarity ranking.
- **Scope creep**: built in phases (below). Each phase works on its own.

## Phases

1. **Core**: parse export, store plays and playlists, profiles with a secret key, overview, top tracks and artists, clock, timeline.
2. **Autobiography**: eras, ghosts, decades, genres, count vs. love, skips.
3. **Fix-it actions**: dead-weight list, ghost playlist, skip-cleanup. Exported as `spotify:track:` URIs you paste straight into a Spotify desktop playlist (Ctrl+V). No API needed.
4. **Social and local**: friend comparison (overlap, compatibility, "who got who into what"), local-scene artist tagging.
5. **Enrichment and AI**: background genre/year lookup, Last.fm recommendations, Ollama ranking and summary.
6. **Rediscovery Mixtape** (class demo feature, end to end): tracks you loved (5+ plays, ≤30% skips) but haven't
   played in N months, max 2 per artist, labelled with the era they peaked in (`stats.rediscoveries`). Ordered as a walk
   through your eras with a template liner note each. Ollama may reorder and rewrite the notes but can't add tracks
   (`recommend.mixtape`). Bonus new picks come from Last.fm similar artists. Saved tapes get a public share link
   (`/?mixtape=<slug>`) that exposes only the tracklist. "Copy to Spotify" reuses the URI export.

Demo data: `scripts/make_sample_export.py` generates a realistic fake export so the
site can be shown without waiting on Spotify.
