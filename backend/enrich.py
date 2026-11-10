"""Every external call lives here, so a source can be swapped if an API changes.

Last.fm: artist genres (top tags), similar artists, artist top tracks.  Needs LASTFM_API_KEY.
MusicBrainz: track release year.  No key; 1 request/sec limit.
Ollama: optional local LLM.  OLLAMA_URL / OLLAMA_MODEL.
Results are cached in artist_info / track_info so each lookup happens once.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import func, select

from db import ArtistInfo, ArtistTopTracks, Play, SessionLocal, TrackInfo

LASTFM_KEY = os.getenv("LASTFM_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")  # empty = first installed model
UA = {"User-Agent": "LinerNotes/0.1 (class project)"}
JUNK_TAGS = {"seen live", "favorites", "favourite", "love", "awesome", "beautiful", "albums i own"}


def _lastfm(method: str, **params):
    if not LASTFM_KEY:
        return None
    try:
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", timeout=10, headers=UA,
                      params={"method": method, "api_key": LASTFM_KEY, "format": "json", **params})
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def lookup_artist(name: str) -> tuple[str, str]:
    tags = _lastfm("artist.gettoptags", artist=name) or {}
    genres = [t["name"].lower() for t in tags.get("toptags", {}).get("tag", [])[:8]
              if t["name"].lower() not in JUNK_TAGS and not t["name"].isdigit()][:4]
    sim = _lastfm("artist.getsimilar", artist=name, limit=15) or {}
    similar = [a["name"] for a in sim.get("similarartists", {}).get("artist", [])]
    return ",".join(genres), "|".join(similar)


def lookup_release_year(artist: str, track: str) -> int | None:
    q = f'recording:"{track}" AND artist:"{artist}"'
    try:
        r = httpx.get("https://musicbrainz.org/ws/2/recording", headers=UA, timeout=10,
                      params={"query": q, "fmt": "json", "limit": 5})
        years = [int(rec["first-release-date"][:4]) for rec in r.json().get("recordings", [])
                 if rec.get("first-release-date", "")[:4].isdigit()]
        return min(years) if years else None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        time.sleep(1.1)  # MusicBrainz rate limit


def _fetch_top_tracks(artist: str) -> list[str] | None:
    """None means the request failed (don't cache); [] means Last.fm has nothing."""
    data = _lastfm("artist.gettoptracks", artist=artist, limit=5)
    if data is None or "toptracks" not in data:
        return None
    return [t["name"] for t in data["toptracks"].get("track", [])]


def top_tracks_many(s, artists: list[str]) -> dict[str, list[str]]:
    """Top tracks for many artists at once: one cache query, then any misses fetched in parallel and saved."""
    artists = list(dict.fromkeys(a for a in artists if a))
    if not artists:
        return {}
    out = {r.artist: [t for t in r.tracks.split("|") if t]
           for r in s.scalars(select(ArtistTopTracks).where(ArtistTopTracks.artist.in_(artists)))}
    misses = [a for a in artists if a not in out]
    if misses and LASTFM_KEY:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for artist, tracks in zip(misses, pool.map(_fetch_top_tracks, misses)):
                if tracks is not None:
                    out[artist] = tracks
                    s.merge(ArtistTopTracks(artist=artist, tracks="|".join(tracks)))
        s.commit()
    return {a: out.get(a, []) for a in artists}


def artist_top_tracks(s, artist: str, limit: int = 5) -> list[str]:
    return top_tracks_many(s, [artist])[artist][:limit]


def enrich_profile(profile_id: int, max_tracks: int = 150):
    """Background job: fill artist genres and release years for this profile's most-played music."""
    with SessionLocal() as s:
        artists = [a for (a,) in s.execute(
            select(Play.artist).where(Play.profile_id == profile_id)
            .group_by(Play.artist).order_by(func.count().desc()).limit(200))]
        for a in artists:
            info = s.get(ArtistInfo, a)
            if info and info.looked_up:
                continue
            if not LASTFM_KEY:
                break
            genres, similar = lookup_artist(a)
            s.merge(ArtistInfo(artist=a, genres=genres, similar=similar, looked_up=True))
            s.commit()

        tracks = s.execute(
            select(Play.artist, Play.track).where(Play.profile_id == profile_id)
            .group_by(Play.artist, Play.track).order_by(func.count().desc()).limit(max_tracks)).all()
        for artist, track in tracks:
            key = f"{artist}\t{track}"
            info = s.get(TrackInfo, key)
            if info and info.looked_up:
                continue
            s.merge(TrackInfo(key=key, release_year=lookup_release_year(artist, track), looked_up=True))
            s.commit()


def _first_model() -> str | None:
    try:
        models = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3).json().get("models", [])
        return models[0]["name"] if models else None
    except (httpx.HTTPError, ValueError):
        return None


def ollama(prompt: str, as_json: bool = False) -> str | None:
    """Returns None if Ollama isn't running, so callers can fall back."""
    model = OLLAMA_MODEL or _first_model()
    if not model:
        return None
    body = {"model": model, "prompt": prompt, "stream": False}
    if as_json:
        body["format"] = "json"
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=90)
        return r.json().get("response") if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def ollama_json(prompt: str):
    raw = ollama(prompt, as_json=True)
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None
