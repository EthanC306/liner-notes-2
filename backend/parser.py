"""Parse a Spotify data export (zip or loose JSON files) entirely in memory.

Handles both formats Spotify ships:
  * Account data:  StreamingHistory_music_N.json  {endTime, artistName, trackName, msPlayed}
  * Extended:      Streaming_History_Audio_*.json {ts, master_metadata_*, ms_played, skipped, ...}
  * Playlists:     Playlist1.json {playlists: [{name, items: [{track: {...}}]}]}
"""
import io
import json
import zipfile
from datetime import datetime

MIN_MS = 5_000  # ignore accidental sub-5s plays


def _parse_dt(s: str) -> datetime:
    s = s.replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"bad date {s!r}")


def _iter_json_files(filename: str, data: bytes):
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".json"):
                    yield name.rsplit("/", 1)[-1], zf.read(name)
    elif filename.lower().endswith(".json"):
        yield filename, data


def parse_export(files: list[tuple[str, bytes]]):
    """Returns (plays, playlist_tracks, has_extended)."""
    plays, playlist_tracks, has_extended = [], [], False
    for fname, raw in files:
        for name, content in _iter_json_files(fname, raw):
            try:
                obj = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            lname = name.lower()
            if lname.startswith("playlist") and isinstance(obj, dict):
                playlist_tracks += _parse_playlists(obj)
            elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
                if "ts" in obj[0]:
                    has_extended = True
                    plays += _parse_extended(obj)
                elif "endTime" in obj[0]:
                    plays += _parse_basic(obj)
    return plays, playlist_tracks, has_extended


def _play(dt, track, artist, album, uri, ms, skipped):
    return {
        "played_at": dt, "track": track[:300], "artist": artist[:300],
        "album": (album or "")[:300], "uri": uri, "ms_played": ms, "skipped": skipped,
        "year": dt.year, "month": dt.strftime("%Y-%m"), "hour": dt.hour, "weekday": dt.weekday(),
    }


def _parse_basic(rows):
    out = []
    for r in rows:
        if r.get("msPlayed", 0) < MIN_MS or not r.get("trackName"):
            continue
        out.append(_play(_parse_dt(r["endTime"]), r["trackName"], r["artistName"],
                         None, None, r["msPlayed"], None))
    return out


def _parse_extended(rows):
    out = []
    for r in rows:
        track = r.get("master_metadata_track_name")
        if not track or r.get("ms_played", 0) < MIN_MS:
            continue  # podcasts / audiobooks have no track name
        skipped = bool(r.get("skipped")) or r.get("reason_end") == "fwdbtn"
        out.append(_play(_parse_dt(r["ts"]), track, r.get("master_metadata_album_artist_name") or "Unknown",
                         r.get("master_metadata_album_album_name"), r.get("spotify_track_uri"),
                         r["ms_played"], skipped))
    return out


def _parse_playlists(obj):
    out = []
    for pl in obj.get("playlists", []):
        for item in pl.get("items", []):
            t = item.get("track") or {}
            if t.get("trackName"):
                out.append({"playlist": pl.get("name", "Untitled")[:300], "track": t["trackName"][:300],
                            "artist": t.get("artistName", "Unknown")[:300],
                            "album": (t.get("albumName") or "")[:300], "uri": t.get("trackUri")})
    return out
