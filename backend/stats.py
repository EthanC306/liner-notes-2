"""All listening stats. Mostly SQL aggregations; the queries are portable (SQLite + Postgres)."""
import math
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import and_, case, func, select

from db import ArtistInfo, LocalTag, Play, PlaylistTrack, TrackInfo

RECENT_DAYS = 180
SKIP_MS = 30_000  # basic export has no skip flag; < 30s counts as a skip

# Extended exports carry a real skip flag; otherwise approximate with play length.
skip_expr = case((Play.skipped.is_not(None), case((Play.skipped, 1), else_=0)),
                 (Play.ms_played < SKIP_MS, 1), else_=0)
minutes = func.sum(Play.ms_played) / 60000.0


class Filters:
    def __init__(self, start: int | None = None, end: int | None = None, genre: str | None = None):
        self.start, self.end, self.genre = start, end, (genre or "").lower().strip() or None

    def where(self, s, pid):
        conds = [Play.profile_id == pid]
        if self.start:
            conds.append(Play.year >= self.start)
        if self.end:
            conds.append(Play.year <= self.end)
        if self.genre:
            artists = [a for (a,) in s.execute(
                select(ArtistInfo.artist).where(ArtistInfo.genres.like(f"%{self.genre}%")))]
            conds.append(Play.artist.in_(artists))
        return and_(*conds)


def _artist_genres(s, artists) -> dict[str, list[str]]:
    rows = s.execute(select(ArtistInfo.artist, ArtistInfo.genres).where(ArtistInfo.artist.in_(list(artists))))
    return {a: [g for g in (gs or "").split(",") if g] for a, gs in rows}


def last_played(s, pid):
    return s.scalar(select(func.max(Play.played_at)).where(Play.profile_id == pid))


def overview(s, pid, f: Filters):
    w = f.where(s, pid)
    r = s.execute(select(func.count(), minutes, func.count(func.distinct(Play.artist)),
                         func.min(Play.played_at), func.max(Play.played_at)).where(w)).one()
    tracks = s.scalar(select(func.count()).select_from(
        select(Play.artist, Play.track).where(w).group_by(Play.artist, Play.track).subquery()))
    years = [y for (y,) in s.execute(select(Play.year).where(Play.profile_id == pid).distinct().order_by(Play.year))]
    genres = Counter()
    for gs in _artist_genres(s, [a for (a,) in s.execute(
            select(Play.artist).where(Play.profile_id == pid).distinct())]).values():
        genres.update(gs[:2])
    return {"plays": r[0], "minutes": round(r[1] or 0), "artists": r[2], "tracks": tracks,
            "first": r[3].isoformat() if r[3] else None, "last": r[4].isoformat() if r[4] else None,
            "years": years, "genres": [g for g, _ in genres.most_common(40)]}


def top_tracks(s, pid, f, by="plays", limit=50):
    order = func.count() if by == "plays" else minutes
    rows = s.execute(select(Play.track, Play.artist, func.count(), minutes, func.sum(skip_expr), func.max(Play.uri))
                     .where(f.where(s, pid)).group_by(Play.track, Play.artist)
                     .order_by(order.desc()).limit(limit))
    return [{"track": t, "artist": a, "plays": n, "minutes": round(m), "skip_rate": round(sk / n, 2), "uri": u}
            for t, a, n, m, sk, u in rows]


def top_artists(s, pid, f, by="minutes", limit=50):
    order = func.count() if by == "plays" else minutes
    rows = s.execute(select(Play.artist, func.count(), minutes, func.min(Play.played_at))
                     .where(f.where(s, pid)).group_by(Play.artist).order_by(order.desc()).limit(limit)).all()
    genres = _artist_genres(s, [r[0] for r in rows])
    return [{"artist": a, "plays": n, "minutes": round(m), "since": first.date().isoformat(),
             "genres": genres.get(a, [])[:3]} for a, n, m, first in rows]


def timeline(s, pid, f, n_artists=6):
    w = f.where(s, pid)
    top = [a for (a,) in s.execute(select(Play.artist).where(w).group_by(Play.artist)
                                    .order_by(minutes.desc()).limit(n_artists))]
    months = [m for (m,) in s.execute(select(Play.month).where(w).distinct().order_by(Play.month))]
    series = {a: dict.fromkeys(months, 0) for a in top}
    for a, m, mins in s.execute(select(Play.artist, Play.month, minutes)
                                .where(w, Play.artist.in_(top)).group_by(Play.artist, Play.month)):
        series[a][m] = round(mins)
    total = {m: round(v) for m, v in s.execute(select(Play.month, minutes).where(w).group_by(Play.month))}
    return {"months": months, "total": [total.get(m, 0) for m in months],
            "series": [{"artist": a, "data": list(series[a].values())} for a in top]}


def clock(s, pid, f):
    grid = [[0] * 24 for _ in range(7)]
    for wd, hr, n in s.execute(select(Play.weekday, Play.hour, func.count())
                               .where(f.where(s, pid)).group_by(Play.weekday, Play.hour)):
        grid[wd][hr] = n
    return {"grid": grid}


def eras(s, pid, f):
    """Each half-year gets a headline: its dominant genre and artist."""
    w = f.where(s, pid)
    period = func.substr(Play.month, 1, 4)
    half = case((func.substr(Play.month, 6, 2) <= "06", "H1"), else_="H2")
    rows = s.execute(select(period, half, Play.artist, minutes).where(w)
                     .group_by(period, half, Play.artist)).all()
    genres = _artist_genres(s, {r[2] for r in rows})
    buckets = defaultdict(lambda: {"artists": Counter(), "genres": Counter(), "minutes": 0.0})
    for year, h, artist, mins in rows:
        b = buckets[(year, h)]
        b["artists"][artist] += mins
        b["minutes"] += mins
        for i, g in enumerate(genres.get(artist, [])[:3]):
            b["genres"][g] += mins / (i + 1)
    out = []
    for (year, h), b in sorted(buckets.items()):
        genre = b["genres"].most_common(1)[0][0] if b["genres"] else None
        artist, amins = b["artists"].most_common(1)[0]
        label = f"{'early' if h == 'H1' else 'late'} {year}"
        out.append({"period": label, "minutes": round(b["minutes"]), "genre": genre,
                    "artist": artist, "artist_share": round(amins / b["minutes"], 2),
                    "headline": f"Your {label} {genre} era" if genre else f"Your {label} {artist} era",
                    "top": [a for a, _ in b["artists"].most_common(5)]})
    return out


def ghosts(s, pid, f, limit=25):
    """Artists with a real past footprint who've gone (nearly) silent in the last RECENT_DAYS."""
    end = last_played(s, pid)
    if not end:
        return []
    cutoff = end - timedelta(days=RECENT_DAYS)
    is_recent = case((Play.played_at >= cutoff, 1), else_=0)
    rows = s.execute(select(Play.artist, func.count(), func.sum(is_recent), minutes, func.max(Play.played_at))
                     .where(f.where(s, pid)).group_by(Play.artist).having(func.count() >= 15)).all()
    out = []
    for artist, n, recent, mins, last in rows:
        past = n - recent
        if past >= 15 and recent <= past * 0.03:
            peak = s.execute(select(Play.month, func.count()).where(Play.profile_id == pid, Play.artist == artist)
                             .group_by(Play.month).order_by(func.count().desc()).limit(1)).first()
            out.append({"artist": artist, "plays": n, "minutes": round(mins), "recent_plays": recent,
                        "peak_month": peak[0], "peak_plays": peak[1], "last_played": last.date().isoformat(),
                        "days_gone": (end - last).days})
    out.sort(key=lambda g: -g["minutes"])
    return out[:limit]


def decades(s, pid, f):
    rows = s.execute(select(Play.artist, Play.track, func.count()).where(f.where(s, pid))
                     .group_by(Play.artist, Play.track)).all()
    years = {k: y for k, y in s.execute(select(TrackInfo.key, TrackInfo.release_year)
                                        .where(TrackInfo.release_year.is_not(None)))}
    dec, unknown, examples = Counter(), 0, defaultdict(Counter)
    for artist, track, n in rows:
        y = years.get(f"{artist}\t{track}")
        if y:
            d = f"{y // 10 * 10}s"
            dec[d] += n
            examples[d][f"{track} — {artist}"] += n
        else:
            unknown += n
    return {"decades": [{"decade": d, "plays": dec[d], "examples": [e for e, _ in examples[d].most_common(3)]}
                        for d in sorted(dec)], "unknown": unknown}


def genres(s, pid, f):
    rows = s.execute(select(Play.artist, minutes).where(f.where(s, pid)).group_by(Play.artist)).all()
    gmap = _artist_genres(s, [a for a, _ in rows])
    primary, unknown = Counter(), 0
    for a, mins in rows:
        gs = gmap.get(a)
        if gs:
            primary[gs[0]] += mins
        else:
            unknown += mins
    return {"genres": [{"genre": g, "minutes": round(m)} for g, m in primary.most_common(20)],
            "unknown_minutes": round(unknown)}


def _track_stats(s, pid, f):
    return s.execute(select(Play.track, Play.artist, func.count(), func.sum(skip_expr), func.avg(Play.ms_played),
                            func.max(Play.ms_played), func.max(Play.uri))
                     .where(f.where(s, pid)).group_by(Play.track, Play.artist)).all()


def _playlist_index(s, pid):
    idx = defaultdict(set)
    for t, a, pl in s.execute(select(PlaylistTrack.track, PlaylistTrack.artist, PlaylistTrack.playlist)
                              .where(PlaylistTrack.profile_id == pid)):
        idx[(t.lower(), a.lower())].add(pl)
    return idx


def count_vs_love(s, pid, f):
    """Loved-but-rare: always finished, never skipped, few plays.  Played-not-loved: frequent and frequently skipped."""
    pls = _playlist_index(s, pid)
    loved, tolerated = [], []
    for t, a, n, sk, avg_ms, max_ms, uri in _track_stats(s, pid, f):
        completion = avg_ms / max_ms if max_ms else 0
        row = {"track": t, "artist": a, "plays": n, "skip_rate": round(sk / n, 2),
               "completion": round(completion, 2), "uri": uri, "playlists": sorted(pls.get((t.lower(), a.lower()), []))}
        if 2 <= n <= 6 and sk == 0 and completion >= 0.9:
            loved.append(row)
        elif n >= 10 and sk / n >= 0.4:
            tolerated.append(row)
    loved.sort(key=lambda r: (-len(r["playlists"]), -r["plays"]))
    tolerated.sort(key=lambda r: -r["skip_rate"] * r["plays"])
    return {"loved_rarely": loved[:30], "played_not_loved": tolerated[:30]}


def skips(s, pid, f):
    pls = _playlist_index(s, pid)
    out = []
    for t, a, n, sk, _avg, _max, uri in _track_stats(s, pid, f):
        if n >= 5 and sk / n >= 0.5:
            out.append({"track": t, "artist": a, "attempts": n, "skips": sk, "skip_rate": round(sk / n, 2),
                        "uri": uri, "playlists": sorted(pls.get((t.lower(), a.lower()), []))})
    out.sort(key=lambda r: (-bool(r["playlists"]), -r["skips"]))
    return out[:60]


def dead_weight(s, pid, days=365):
    """Playlist tracks you haven't played in `days`."""
    end = last_played(s, pid)
    if not end:
        return []
    recent = {(t.lower(), a.lower()) for t, a in s.execute(
        select(Play.track, Play.artist).where(Play.profile_id == pid, Play.played_at >= end - timedelta(days=days))
        .distinct())}
    by_pl = defaultdict(list)
    for pl, t, a, uri in s.execute(select(PlaylistTrack.playlist, PlaylistTrack.track, PlaylistTrack.artist,
                                          PlaylistTrack.uri).where(PlaylistTrack.profile_id == pid)):
        if (t.lower(), a.lower()) not in recent:
            by_pl[pl].append({"track": t, "artist": a, "uri": uri})
    sizes = Counter(pl for (pl,) in s.execute(select(PlaylistTrack.playlist).where(PlaylistTrack.profile_id == pid)))
    return [{"playlist": pl, "size": sizes[pl], "dead": tracks} for pl, tracks in
            sorted(by_pl.items(), key=lambda kv: -len(kv[1]) / sizes[kv[0]])]


def ghost_playlist(s, pid, per_artist=3):
    out = []
    for g in ghosts(s, pid, Filters(), limit=15):
        for t, uri, n in s.execute(select(Play.track, func.max(Play.uri), func.count())
                                   .where(Play.profile_id == pid, Play.artist == g["artist"])
                                   .group_by(Play.track).order_by(func.count().desc()).limit(per_artist)):
            out.append({"track": t, "artist": g["artist"], "uri": uri, "plays": n})
    return out


def artist_vector(s, pid) -> dict[str, float]:
    rows = s.execute(select(Play.artist, minutes).where(Play.profile_id == pid).group_by(Play.artist)).all()
    total = sum(m for _, m in rows) or 1
    return {a: m / total for a, m in rows}


def compare(s, pid_a, pid_b):
    va, vb = artist_vector(s, pid_a), artist_vector(s, pid_b)
    dot = sum(va[a] * vb[a] for a in va.keys() & vb.keys())
    norm = math.sqrt(sum(v * v for v in va.values())) * math.sqrt(sum(v * v for v in vb.values()))
    shared = sorted(va.keys() & vb.keys(), key=lambda a: -(va[a] * vb[a]))
    first = lambda pid: dict(s.execute(select(Play.artist, func.min(Play.played_at))  # noqa: E731
                                       .where(Play.profile_id == pid).group_by(Play.artist)).all())
    fa, fb = first(pid_a), first(pid_b)
    intros = []
    for a in shared[:60]:
        gap = (fb[a] - fa[a]).days
        if abs(gap) >= 30:
            intros.append({"artist": a, "first_by": "a" if gap > 0 else "b", "days_ahead": abs(gap)})
    ga, gb = Counter(), Counter()
    gmap = _artist_genres(s, va.keys() | vb.keys())
    for vec, gc in ((va, ga), (vb, gb)):
        for a, w in vec.items():
            for g in gmap.get(a, [])[:1]:
                gc[g] += w
    return {"compatibility": round(100 * dot / norm) if norm else 0,
            "shared_count": len(shared), "only_a": len(va) - len(shared), "only_b": len(vb) - len(shared),
            "shared": [{"artist": a, "a_share": round(va[a] * 100, 1), "b_share": round(vb[a] * 100, 1)}
                       for a in shared[:20]],
            "intros": intros[:15],
            "genres_a": [g for g, _ in ga.most_common(6)], "genres_b": [g for g, _ in gb.most_common(6)]}


def local_scene(s, pid):
    tags = s.execute(select(LocalTag.id, LocalTag.artist, LocalTag.city).where(LocalTag.profile_id == pid)).all()
    counts = dict(s.execute(select(Play.artist, func.count()).where(
        Play.profile_id == pid, Play.artist.in_([t[1] for t in tags])).group_by(Play.artist)).all())
    return [{"id": i, "artist": a, "city": c, "plays": counts.get(a, 0)} for i, a, c in tags]
