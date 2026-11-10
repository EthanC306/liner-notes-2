"""End-to-end API tests on the generated sample export. Run: pytest (from repo root)."""
import io
import json
import os
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest

os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:1")  # never hit a real LLM in tests
ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'tests' / 'test.db'}")
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture(scope="session")
def client():
    db = ROOT / "tests" / "test.db"
    db.unlink(missing_ok=True)
    subprocess.run([sys.executable, "scripts/make_sample_export.py", "--seed-db"], cwd=ROOT, check=True, env=os.environ)
    subprocess.run([sys.executable, "scripts/make_sample_export.py", "--friend"], cwd=ROOT, check=True, env=os.environ)
    from fastapi.testclient import TestClient
    from main import app
    yield TestClient(app)
    db.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def keys(client):
    r = client.post("/api/demo").json()
    return {"X-Profile-Key": r["key"]}, r["friend_key"]


def test_basic_export_and_merge(client):
    basic = [{"endTime": "2024-03-01 10:00", "artistName": "A", "trackName": "Song", "msPlayed": 200000},
             {"endTime": "2024-03-01 10:05", "artistName": "A", "trackName": "Song", "msPlayed": 1000}]  # < 5s dropped
    files = [("files", ("StreamingHistory_music_0.json", json.dumps(basic)))]
    r = client.post("/api/upload", files=files).json()
    assert r["plays_parsed"] == 1 and not r["extended"]
    h = {"X-Profile-Key": r["key"]}
    client.post("/api/upload", files=files, headers=h)  # re-upload must not duplicate
    assert client.get("/api/stats/overview", headers=h).json()["plays"] == 1


def test_rejects_garbage(client):
    assert client.post("/api/upload", files=[("files", ("x.json", "{}"))]).status_code == 400
    assert client.get("/api/me", headers={"X-Profile-Key": "nope"}).status_code == 401


def test_stats(client, keys):
    h, _ = keys
    ghosts = [g["artist"] for g in client.get("/api/stats/ghosts", headers=h).json()]
    assert "Brand New" in ghosts and "Kendrick Lamar" not in ghosts
    eras = client.get("/api/stats/eras", headers=h).json()
    assert eras[0]["genre"] == "indie folk" and any(e["genre"] == "emo" for e in eras)
    skips = client.get("/api/stats/skips", headers=h).json()
    assert skips[0]["track"] in {"Creep", "Thnks fr th Mmrs", "Let It Be"} and skips[0]["playlists"]
    assert client.get("/api/stats/love", headers=h).json()["loved_rarely"]
    decades = {d["decade"] for d in client.get("/api/stats/decades", headers=h).json()["decades"]}
    assert {"1960s", "1970s", "2020s"} <= decades
    emo = client.get("/api/stats/top?genre=emo", headers=h).json()["artists"]
    assert emo and all("emo" in a["genres"] for a in emo)


def test_actions_and_compare(client, keys):
    h, friend = keys
    dead = client.get("/api/actions/dead-weight", headers=h).json()
    assert any(p["playlist"] == "sad girl autumn" for p in dead)
    c = client.get(f"/api/compare?friend_key={friend}", headers=h).json()
    assert 0 < c["compatibility"] < 100 and c["shared_count"] > 0
    recs = client.get("/api/recommendations?ai=false", headers=h).json()["items"]
    played = {a["artist"] for a in client.get("/api/stats/top", headers=h).json()["artists"]}
    assert recs and not {r["artist"] for r in recs} & played


def test_delete_everything(client):
    files = [("files", ("StreamingHistory_music_0.json",
                        json.dumps([{"endTime": "2024-03-01 10:00", "artistName": "A", "trackName": "S", "msPlayed": 99999}])))]
    h = {"X-Profile-Key": client.post("/api/upload", files=files).json()["key"]}
    assert client.delete("/api/me", headers=h).json()["deleted"]
    assert client.get("/api/me", headers=h).status_code == 401


def test_mixtape(client, keys):
    h, friend = keys
    m = client.get("/api/mixtape?length=10&months_gone=6&ai=false", headers=h).json()
    tracks = m["tracks"]
    assert 0 < len(tracks) <= 10 and not m["ai"]
    assert all(t["days_gone"] >= 180 and t["skip_rate"] <= 0.3 and t["note"] for t in tracks)
    assert max(Counter(t["artist"] for t in tracks).values()) <= 2  # one artist can't take over
    assert [t["peak_month"] for t in tracks] == sorted(t["peak_month"] for t in tracks)  # walk through time

    # Save, share publicly, and only the owner can delete.
    slug = client.post("/api/mixtape", headers=h, json={"title": "Side B", "tracks": tracks}).json()["slug"]
    shared = client.get(f"/api/mixtape/{slug}").json()  # no key needed
    assert shared["title"] == "Side B" and shared["tracks"][0]["track"] == tracks[0]["track"]
    assert set(shared["tracks"][0]) == {"track", "artist", "uri", "era", "note"}  # nothing else leaks
    assert any(x["slug"] == slug for x in client.get("/api/mixtapes", headers=h).json())
    client.delete(f"/api/mixtape/{slug}", headers={"X-Profile-Key": friend})
    assert client.get(f"/api/mixtape/{slug}").status_code == 200
    client.delete(f"/api/mixtape/{slug}", headers=h)
    assert client.get(f"/api/mixtape/{slug}").status_code == 404
    assert client.post("/api/mixtape", headers=h, json={"title": "x", "tracks": []}).status_code == 400


def test_mixtape_ai_cannot_invent(monkeypatch):
    import enrich, recommend
    from db import SessionLocal, Profile
    from sqlalchemy import select
    monkeypatch.setattr(enrich, "ollama_json", lambda prompt: {
        "title": "Ghosts of 2022", "order": [{"index": 1, "note": "the good one"}, {"index": 99, "note": "fake"},
                                            {"index": "0", "note": "bad type"}]})
    with SessionLocal() as s:
        pid = s.scalar(select(Profile.id).where(Profile.name == "Demo Listener"))
        base = recommend.mixtape(s, pid, 8, 6, use_ai=False)["tracks"]
        m = recommend.mixtape(s, pid, 8, 6, use_ai=True)
    assert m["ai"] and m["title"] == "Ghosts of 2022"
    assert m["tracks"][0]["track"] == base[1]["track"] and m["tracks"][0]["note"] == "the good one"
    assert sorted(t["track"] for t in m["tracks"]) == sorted(t["track"] for t in base)  # same set, nothing invented
