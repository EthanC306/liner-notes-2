"""Quick setup through the Spotify Web API.

The user connects Spotify once; we fetch a snapshot (top tracks and artists for three time ranges, the last 50 plays,
and the songs in playlists they own) and store it. Access tokens are used for that one fetch and never saved.

Development Mode limits (Feb 2026): at most 5 users, each added in the Spotify developer dashboard, and the app owner
needs Premium. The redirect URI must match one registered in the dashboard exactly (Spotify rejects "localhost";
use http://127.0.0.1:8000/api/spotify/callback for local runs).
"""
import base64
import json
import os
import secrets
import time
from collections import Counter
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from db import PlaylistTrack, Profile, SessionLocal, SpotifySnapshot

router = APIRouter(prefix="/api/spotify")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "user-top-read user-read-recently-played playlist-read-private playlist-read-collaborative"
RANGES = {"short_term": "Last 4 weeks", "medium_term": "Last 6 months", "long_term": "Last year"}
MAX_PLAYLISTS = 30
STATE_TTL = 600

# state -> (profile id, redirect uri, expiry). One server process, so memory is enough for a 10-minute login hop.
_pending: dict[str, tuple[int, str, float]] = {}


class SpotifyError(Exception):
    pass


def db():
    with SessionLocal() as s:
        yield s


def fake_mode() -> bool:
    """SPOTIFY_FAKE=1: no Spotify app needed. Connect skips Spotify's login and uses a built-in sample snapshot,
    so the quick-setup flow can be tried and demoed without keys."""
    return os.getenv("SPOTIFY_FAKE", "").lower() in ("1", "true", "yes")


def _creds():
    if fake_mode():
        return ("fake", "fake")
    cid, secret = os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def _redirect_uri(request: Request) -> str:
    return os.getenv("SPOTIFY_REDIRECT_URI") or f"{str(request.base_url).rstrip('/')}/api/spotify/callback"


# ---------- HTTP (kept small so tests can replace them) ----------
def exchange_code(code: str, redirect_uri: str) -> str:
    if fake_mode():
        return "fake-token"
    cid, secret = _creds()
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = httpx.post(TOKEN_URL, timeout=15, headers={"Authorization": f"Basic {auth}"},
                   data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
    if r.status_code != 200:
        raise SpotifyError("Spotify didn't accept the login. Try connecting again.")
    return r.json()["access_token"]


def api_get(token: str, path: str, **params) -> dict:
    if fake_mode():
        return _fake_get(path, params)
    r = httpx.get(f"{API}{path}", params=params, timeout=15, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 403:
        raise SpotifyError("Spotify refused access. While the app is in Development Mode, your Spotify account "
                           "has to be added as a user in the app's Spotify developer dashboard (5 users max).")
    if r.status_code == 429:
        raise SpotifyError("Spotify is rate limiting us. Wait a minute and connect again.")
    r.raise_for_status()
    return r.json()


# ---------- Fake Spotify (SPOTIFY_FAKE=1) ----------
def _fake_track(name, artist, year):
    return {"name": name, "type": "track", "uri": None, "artists": [{"name": artist}],
            "album": {"name": "", "release_date": f"{year}-01-01", "images": []}}


_FAKE_SONGS = [("Motion Sickness", "Phoebe Bridgers", 2017), ("Pink Pony Club", "Chappell Roan", 2020),
               ("Nights", "Frank Ocean", 2016), ("Bags", "Clairo", 2019), ("Dreams", "Fleetwood Mac", 1977),
               ("Kyoto", "Phoebe Bridgers", 2020), ("Seventeen", "Sharon Van Etten", 2019), ("Ivy", "Frank Ocean", 2016),
               ("Cherry Wine", "Hozier", 2016), ("Heroes", "David Bowie", 1977), ("Silver Springs", "Fleetwood Mac", 1976),
               ("Good Luck, Babe!", "Chappell Roan", 2024)]
_FAKE_ARTISTS = [("Phoebe Bridgers", ["indie folk", "indie pop"]), ("Chappell Roan", ["pop", "indie pop"]),
                 ("Frank Ocean", ["r&b", "neo soul"]), ("Fleetwood Mac", ["soft rock", "classic rock"]),
                 ("Clairo", ["bedroom pop", "indie pop"]), ("Hozier", ["irish singer-songwriter", "indie folk"]),
                 ("David Bowie", ["glam rock", "art rock"]), ("Sharon Van Etten", ["indie folk", "chamber pop"])]


def _fake_get(path: str, params: dict) -> dict:
    if path == "/me":
        return {"id": "fake-user", "display_name": "Sample Spotify listener"}
    if path == "/me/top/tracks":  # shuffle the order a little per range so the range buttons visibly change
        shift = list(RANGES).index(params["time_range"]) * 3
        return {"items": [_fake_track(*x) for x in _FAKE_SONGS[shift:] + _FAKE_SONGS[:shift]]}
    if path == "/me/top/artists":
        shift = list(RANGES).index(params["time_range"]) * 2
        return {"items": [{"name": a, "genres": g, "images": []} for a, g in _FAKE_ARTISTS[shift:] + _FAKE_ARTISTS[:shift]]}
    if path == "/me/player/recently-played":
        return {"items": [{"track": _fake_track(*_FAKE_SONGS[i % len(_FAKE_SONGS)]),
                           "played_at": f"2026-11-{14 - i // 4:02d}T{20 - i % 4 * 3:02d}:15:00Z"} for i in range(20)]}
    if path == "/me/playlists":
        return {"items": [{"id": "fake1", "name": "Late night", "owner": {"id": "fake-user"}},
                          {"id": "fake2", "name": "Someone else's mix", "owner": {"id": "other"}}]}
    if path == "/playlists/fake1/items":
        return {"items": [{"item": _fake_track(*x)} for x in _FAKE_SONGS[2:8]], "next": None}
    raise SpotifyError(f"Fake Spotify has no data for {path}")


# ---------- Snapshot ----------
def _track(t: dict) -> dict:
    album = t.get("album") or {}
    return {"track": t["name"][:300], "artist": (t.get("artists") or [{}])[0].get("name", "Unknown")[:300],
            "album": album.get("name", "")[:300], "uri": t.get("uri"),
            "year": int(album["release_date"][:4]) if (album.get("release_date") or "")[:4].isdigit() else None,
            "image": next((i["url"] for i in album.get("images", [])[-1:]), None)}


def fetch_snapshot(token: str) -> tuple[dict, list[dict]]:
    """Returns (snapshot, playlist_tracks) for one connected user."""
    me = api_get(token, "/me")
    top = {}
    for rng in RANGES:
        tracks = api_get(token, "/me/top/tracks", time_range=rng, limit=50)["items"]
        artists = api_get(token, "/me/top/artists", time_range=rng, limit=50)["items"]
        top[rng] = {"tracks": [_track(t) for t in tracks],
                    "artists": [{"artist": a["name"], "genres": a.get("genres", [])[:4],
                                 "image": next((i["url"] for i in a.get("images", [])[-1:]), None)} for a in artists]}
    recent = [{**_track(i["track"]), "played_at": i["played_at"]}
              for i in api_get(token, "/me/player/recently-played", limit=50)["items"] if i.get("track")]

    # Playlist songs are only readable for playlists the user owns or collaborates on.
    playlist_tracks, playlists = [], []
    for pl in api_get(token, "/me/playlists", limit=50)["items"][:MAX_PLAYLISTS]:
        if not pl or ((pl.get("owner") or {}).get("id") != me.get("id") and not pl.get("collaborative")):
            continue
        rows = []
        path, params = f"/playlists/{pl['id']}/items", {"limit": 100}
        for _ in range(5):  # up to 500 songs per playlist
            page = api_get(token, path, **params)
            for it in page.get("items", []):
                t = it.get("item") or it.get("track")
                if t and t.get("type", "track") == "track" and t.get("name"):
                    tr = _track(t)
                    rows.append({"playlist": pl["name"][:300], "track": tr["track"], "artist": tr["artist"],
                                 "album": tr["album"], "uri": tr["uri"]})
            if not page.get("next"):
                break
            params = {"limit": 100, "offset": params.get("offset", 0) + 100}
        playlists.append({"name": pl["name"], "tracks": len(rows)})
        playlist_tracks += rows
    return {"name": me.get("display_name") or "", "top": top, "recent": recent, "playlists": playlists}, playlist_tracks


def quick_view(snapshot: dict) -> dict:
    """What the quick pages show: top lists per range plus genre and decade breakdowns."""
    long = snapshot["top"].get("long_term") or snapshot["top"].get("medium_term") or {"tracks": [], "artists": []}
    genres = Counter()
    for i, a in enumerate(long["artists"]):
        for g in a["genres"][:2]:
            genres[g] += 50 - i  # higher-ranked artists count more
    decades = Counter(f"{t['year'] // 10 * 10}s" for t in long["tracks"] if t.get("year"))
    return {"ranges": RANGES, "top": snapshot["top"], "recent": snapshot["recent"], "playlists": snapshot["playlists"],
            "genres": genres.most_common(15), "decades": sorted(decades.items())}


# ---------- Routes ----------
@router.get("/status")
def status():
    return {"configured": _creds() is not None, "fake": fake_mode()}


@router.post("/start")
def start(request: Request, x_profile_key: str = Header(...), s=Depends(db)):
    if not _creds():
        raise HTTPException(503, "Spotify isn't set up on this server (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET).")
    p = s.scalar(select(Profile).where(Profile.secret == x_profile_key))
    if not p:
        raise HTTPException(401, "Unknown profile key")
    now = time.time()
    for k in [k for k, v in _pending.items() if v[2] < now]:
        del _pending[k]
    state, redirect_uri = secrets.token_urlsafe(24), _redirect_uri(request)
    _pending[state] = (p.id, redirect_uri, now + STATE_TTL)
    if fake_mode():  # skip Spotify's login page and come straight back, as if the user had approved
        return {"url": "/api/spotify/callback?" + urlencode({"state": state, "code": "fake"})}
    return {"url": f"{AUTH_URL}?" + urlencode({"client_id": _creds()[0], "response_type": "code", "scope": SCOPES,
                                               "redirect_uri": redirect_uri, "state": state})}


@router.get("/callback")
def callback(state: str = "", code: str = "", error: str = "", s=Depends(db)):
    pending = _pending.pop(state, None)
    if error or not pending or pending[2] < time.time():
        msg = "Spotify connection was cancelled." if error else "That Spotify login expired. Try connecting again."
        return RedirectResponse("/?" + urlencode({"spotify_error": msg}), status_code=303)
    pid, redirect_uri, _ = pending
    try:
        snapshot, playlist_tracks = fetch_snapshot(exchange_code(code, redirect_uri))
    except SpotifyError as e:
        return RedirectResponse("/?" + urlencode({"spotify_error": str(e)}), status_code=303)
    except httpx.HTTPError:
        return RedirectResponse("/?" + urlencode({"spotify_error": "Couldn't reach Spotify. Try again."}), status_code=303)
    s.merge(SpotifySnapshot(profile_id=pid, data=json.dumps(snapshot)))
    if playlist_tracks:  # same table the export fills; a later export upload replaces these
        s.execute(delete(PlaylistTrack).where(PlaylistTrack.profile_id == pid))
        s.add_all(PlaylistTrack(profile_id=pid, **t) for t in playlist_tracks)
    s.commit()
    return RedirectResponse("/home?spotify=connected", status_code=303)


@router.get("/quick")
def quick(x_profile_key: str = Header(...), s=Depends(db)):
    p = s.scalar(select(Profile).where(Profile.secret == x_profile_key))
    if not p:
        raise HTTPException(401, "Unknown profile key")
    snap = s.get(SpotifySnapshot, p.id)
    if not snap:
        raise HTTPException(404, "Spotify isn't connected for this profile.")
    return {**quick_view(json.loads(snap.data)), "fetched_at": snap.fetched_at.isoformat()}
