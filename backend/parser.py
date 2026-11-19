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


class ExportError(ValueError):
    """The upload isn't a readable Spotify export; the message is safe to show the user."""


HOW_TO = "Upload the .zip from Spotify (Account > Privacy > Download your data), or the JSON files inside it."


def _iter_json_files(filename: str, data: bytes):
    label = filename or "That file"
    if not data:
        raise ExportError(f"{label} is empty. {HOW_TO}")
    if filename.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            with zf:
                for name in zf.namelist():
                    if name.lower().endswith(".json"):
                        yield name.rsplit("/", 1)[-1], zf.read(name)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, NotImplementedError, RuntimeError):
            raise ExportError(f"{label} isn't a valid zip file (it may be damaged or incomplete). {HOW_TO}")
    elif filename.lower().endswith(".json"):
        yield filename, data
    else:
        raise ExportError(f"{label} isn't a .zip or .json file. {HOW_TO}")


def parse_export(files: list[tuple[str, bytes]]):
    """Returns (plays, playlist_tracks, has_extended)."""
    out = {"plays": [], "playlist_tracks": [], "extended": False}
    for fname, raw in files:
        for name, content in _iter_json_files(fname, raw):
            try:
                obj = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            try:
                _parse_one(name, obj, out)
            except (KeyError, ValueError, TypeError, AttributeError):
                raise ExportError(f"{name} doesn't look like Spotify listening history in a format we can read. {HOW_TO}")
    return out["plays"], out["playlist_tracks"], out["extended"]


def _parse_one(name, obj, out):
    lname = name.lower()
    if lname.startswith("playlist") and isinstance(obj, dict):
        out["playlist_tracks"] += _parse_playlists(obj)
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        if "ts" in obj[0]:
            out["extended"] = True
            out["plays"] += _parse_extended(obj)
        elif "endTime" in obj[0]:
            out["plays"] += _parse_basic(obj)


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
