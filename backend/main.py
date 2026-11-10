"""Liner Notes API. Run: uvicorn main:app --reload  (from backend/)."""
import json
import secrets
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

import enrich
import recommend
import stats
from db import LocalTag, Mixtape, Play, PlaylistTrack, Profile, SessionLocal, engine, init_db
from parser import parse_export

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
MAX_UPLOAD = 200 * 1024 * 1024

app = FastAPI(title="Liner Notes")
init_db()


def db():
    with SessionLocal() as s:
        yield s


def profile(x_profile_key: str = Header(...), s=Depends(db)) -> Profile:
    p = s.scalar(select(Profile).where(Profile.secret == x_profile_key))
    if not p:
        raise HTTPException(401, "Unknown profile key")
    return p


def filters(start: int | None = None, end: int | None = None, genre: str | None = None):
    return stats.Filters(start, end, genre)


def _insert_plays(s, pid, plays):
    """Insert, skipping duplicates, so re-uploading (or adding extended history later) merges cleanly."""
    if not plays:
        return
    ins = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    for i in range(0, len(plays), 2000):
        s.execute(ins(Play).values([{**p, "profile_id": pid} for p in plays[i:i + 2000]])
                  .on_conflict_do_nothing(index_elements=["profile_id", "played_at", "track", "artist"]))


@app.post("/api/upload")
async def upload(background: BackgroundTasks, files: list[UploadFile] = File(...), name: str = Form("Listener"),
                 x_profile_key: str | None = Header(None), s=Depends(db)):
    # Read into memory only; raw uploads are never written to disk.
    blobs = []
    for f in files:
        data = await f.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File too large")
        blobs.append((f.filename or "", data))
    p = s.scalar(select(Profile).where(Profile.secret == x_profile_key)) if x_profile_key else None
    return _ingest(s, background, blobs, name, p)


def _ingest(s, background, blobs, name, p=None):
    plays, playlist_tracks, extended = parse_export(blobs)
    if not plays and not playlist_tracks:
        raise HTTPException(400, "No Spotify streaming history or playlists found in those files.")
    if not p:
        p = Profile(name=name.strip()[:80] or "Listener", secret=secrets.token_urlsafe(24))
        s.add(p)
        s.flush()
    p.has_extended = p.has_extended or extended
    _insert_plays(s, p.id, plays)
    if playlist_tracks:  # playlists are a snapshot: replace
        s.execute(delete(PlaylistTrack).where(PlaylistTrack.profile_id == p.id))
        s.add_all(PlaylistTrack(profile_id=p.id, **t) for t in playlist_tracks)
    s.commit()
    background.add_task(enrich.enrich_profile, p.id)
    return {"key": p.secret, "name": p.name, "plays_parsed": len(plays),
            "playlist_tracks": len(playlist_tracks), "extended": p.has_extended}


@app.post("/api/demo")
def demo(background: BackgroundTasks, s=Depends(db)):
    """Load the generated sample listener (and a friend to compare against)."""
    sample, friend = ROOT / "sample_export.zip", ROOT / "friend_export.zip"
    if not sample.exists():
        raise HTTPException(404, "No demo data. Run: python scripts/make_sample_export.py --seed-db")
    out = _ingest(s, background, [(sample.name, sample.read_bytes())], "Demo Listener")
    if friend.exists():
        out["friend_key"] = _ingest(s, background, [(friend.name, friend.read_bytes())], "Demo Friend")["key"]
    return out


@app.get("/api/me")
def me(p=Depends(profile)):
    return {"name": p.name, "extended": p.has_extended}


@app.delete("/api/me")
def delete_me(p=Depends(profile), s=Depends(db)):
    for model in (Play, PlaylistTrack, LocalTag, Mixtape):
        s.execute(delete(model).where(model.profile_id == p.id))
    s.execute(delete(Profile).where(Profile.id == p.id))
    s.commit()
    return {"deleted": True}


def stat_route(path, fn):
    @app.get(f"/api/stats/{path}", name=path)
    def route(p=Depends(profile), f=Depends(filters), s=Depends(db)):
        return fn(s, p.id, f)


stat_route("overview", stats.overview)
stat_route("timeline", stats.timeline)
stat_route("clock", stats.clock)
stat_route("eras", stats.eras)
stat_route("ghosts", stats.ghosts)
stat_route("decades", stats.decades)
stat_route("genres", stats.genres)
stat_route("love", stats.count_vs_love)
stat_route("skips", stats.skips)


@app.get("/api/stats/top")
def top(by: str = "plays", p=Depends(profile), f=Depends(filters), s=Depends(db)):
    return {"tracks": stats.top_tracks(s, p.id, f, by), "artists": stats.top_artists(s, p.id, f, by)}


@app.get("/api/actions/dead-weight")
def dead_weight(days: int = 365, p=Depends(profile), s=Depends(db)):
    return stats.dead_weight(s, p.id, days)


@app.get("/api/actions/ghost-playlist")
def ghost_playlist(p=Depends(profile), s=Depends(db)):
    return stats.ghost_playlist(s, p.id)


class TrackList(BaseModel):
    tracks: list[dict]


@app.post("/api/actions/export", response_class=PlainTextResponse)
def export_tracks(body: TrackList):
    """spotify:track: URIs paste straight into a Spotify desktop playlist; text lines for the rest."""
    uris = [t["uri"] for t in body.tracks if t.get("uri")]
    rest = [f"{t.get('artist', '')} - {t.get('track', '')}" for t in body.tracks if not t.get("uri")]
    return "\n".join(uris + (["", "# No URI in your export for these (search manually):"] + rest if rest else []))


@app.get("/api/recommendations")
def recommendations(ai: bool = True, p=Depends(profile), s=Depends(db)):
    return recommend.recommend(s, p.id, ai)


@app.get("/api/summary")
def summary(p=Depends(profile), s=Depends(db)):
    return recommend.summary(s, p.id)


@app.get("/api/compare")
def compare(friend_key: str, p=Depends(profile), s=Depends(db)):
    friend = s.scalar(select(Profile).where(Profile.secret == friend_key))
    if not friend:
        raise HTTPException(404, "No profile with that key")
    return {"a": p.name, "b": friend.name, **stats.compare(s, p.id, friend.id)}


@app.get("/api/mixtape")
def mixtape(length: int = 15, months_gone: int = 6, ai: bool = True, p=Depends(profile), s=Depends(db)):
    return recommend.mixtape(s, p.id, max(5, min(length, 40)), max(1, min(months_gone, 36)), ai)


class MixtapeIn(BaseModel):
    title: str
    tracks: list[dict]


MIXTAPE_FIELDS = ("track", "artist", "uri", "era", "note")


@app.post("/api/mixtape")
def mixtape_save(body: MixtapeIn, p=Depends(profile), s=Depends(db)):
    if not body.tracks:
        raise HTTPException(400, "Empty mixtape")
    tracks = [{k: str(t.get(k) or "")[:300] for k in MIXTAPE_FIELDS} for t in body.tracks[:40]]
    m = Mixtape(profile_id=p.id, slug=secrets.token_urlsafe(9), title=body.title.strip()[:120] or "Rediscovery",
                tracks=json.dumps(tracks))
    s.add(m)
    s.commit()
    return {"slug": m.slug}


@app.get("/api/mixtapes")
def mixtape_list(p=Depends(profile), s=Depends(db)):
    rows = s.scalars(select(Mixtape).where(Mixtape.profile_id == p.id).order_by(Mixtape.created_at.desc()))
    return [{"slug": m.slug, "title": m.title, "count": len(json.loads(m.tracks)),
             "created": m.created_at.date().isoformat()} for m in rows]


@app.get("/api/mixtape/{slug}")
def mixtape_shared(slug: str, s=Depends(db)):
    """Public: anyone with the link sees the tracklist, but nothing else about the owner's history."""
    m = s.scalar(select(Mixtape).where(Mixtape.slug == slug))
    if not m:
        raise HTTPException(404, "No mixtape at that link")
    owner = s.get(Profile, m.profile_id)
    return {"title": m.title, "by": owner.name if owner else "", "tracks": json.loads(m.tracks),
            "created": m.created_at.date().isoformat()}


@app.delete("/api/mixtape/{slug}")
def mixtape_delete(slug: str, p=Depends(profile), s=Depends(db)):
    s.execute(delete(Mixtape).where(Mixtape.slug == slug, Mixtape.profile_id == p.id))
    s.commit()
    return {"deleted": True}


class LocalIn(BaseModel):
    artist: str
    city: str


@app.get("/api/local")
def local_list(p=Depends(profile), s=Depends(db)):
    return stats.local_scene(s, p.id)


@app.post("/api/local")
def local_add(body: LocalIn, p=Depends(profile), s=Depends(db)):
    s.add(LocalTag(profile_id=p.id, artist=body.artist.strip()[:300], city=body.city.strip()[:120]))
    s.commit()
    return stats.local_scene(s, p.id)


@app.delete("/api/local/{tag_id}")
def local_delete(tag_id: int, p=Depends(profile), s=Depends(db)):
    s.execute(delete(LocalTag).where(LocalTag.id == tag_id, LocalTag.profile_id == p.id))
    s.commit()
    return stats.local_scene(s, p.id)


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
