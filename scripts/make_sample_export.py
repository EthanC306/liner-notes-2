"""Generate a realistic fake Spotify export so the site can be demoed without waiting for Spotify.

    python scripts/make_sample_export.py                 -> sample_export.zip  (you)
    python scripts/make_sample_export.py --friend        -> friend_export.zip  (for Compare)
    python scripts/make_sample_export.py --seed-db       also seeds genre/year/similar-artist cache
                                                         so decades, genres and recs work offline
"""
import argparse
import hashlib
import io
import json
import random
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

# artist: (genres, similar artists, [(track, release year)])
CATALOG = {
    "Phoebe Bridgers": ("indie folk,indie,singer-songwriter", "Julien Baker|Lucy Dacus|boygenius|Mitski|Big Thief|Adrianne Lenker",
                        [("Motion Sickness", 2017), ("Kyoto", 2020), ("Garden Song", 2020), ("Moon Song", 2020)]),
    "Bon Iver": ("indie folk,folk,indie", "Sufjan Stevens|Fleet Foxes|Big Thief|The National|Iron & Wine",
                 [("Skinny Love", 2007), ("Holocene", 2011), ("Flume", 2007), ("Hey, Ma", 2019)]),
    "Fleet Foxes": ("indie folk,folk,indie", "Bon Iver|Iron & Wine|Sufjan Stevens|Grizzly Bear|Big Thief",
                    [("White Winter Hymnal", 2008), ("Helplessness Blues", 2011), ("Mykonos", 2008)]),
    "My Chemical Romance": ("emo,rock,alternative", "Fall Out Boy|Paramore|Taking Back Sunday|The Used|Brand New",
                            [("Welcome to the Black Parade", 2006), ("Helena", 2004), ("Teenagers", 2006), ("I'm Not Okay (I Promise)", 2004)]),
    "Paramore": ("emo,pop punk,rock", "Fall Out Boy|My Chemical Romance|Hayley Williams|Tonight Alive|The Used",
                 [("Misery Business", 2007), ("Decode", 2008), ("Still into You", 2013), ("This Is Why", 2023)]),
    "Fall Out Boy": ("emo,pop punk,rock", "Paramore|My Chemical Romance|Panic! at the Disco|Taking Back Sunday",
                     [("Sugar, We're Goin Down", 2005), ("Dance, Dance", 2005), ("Thnks fr th Mmrs", 2007)]),
    "Brand New": ("emo,indie rock,alternative", "Taking Back Sunday|Manchester Orchestra|The Used|Jimmy Eat World",
                  [("Jesus Christ", 2006), ("The Quiet Things That No One Ever Knows", 2003), ("Sic Transit Gloria", 2003)]),
    "Kendrick Lamar": ("hip-hop,rap,west coast", "SZA|Tyler, The Creator|J. Cole|Baby Keem|Schoolboy Q|Vince Staples",
                       [("Not Like Us", 2024), ("HUMBLE.", 2017), ("Money Trees", 2012), ("luther", 2024), ("squabble up", 2024)]),
    "SZA": ("rnb,soul,hip-hop", "Kendrick Lamar|Frank Ocean|Daniel Caesar|Summer Walker|Jhené Aiko",
            [("Kill Bill", 2022), ("Snooze", 2022), ("Good Days", 2020), ("Saturn", 2024)]),
    "Tyler, The Creator": ("hip-hop,rap,alternative hip-hop", "Frank Ocean|Steve Lacy|Kendrick Lamar|Kali Uchis|Brent Faiyaz",
                           [("EARFQUAKE", 2019), ("See You Again", 2017), ("St. Chroma", 2024), ("Sticky", 2024)]),
    "Frank Ocean": ("rnb,soul,alternative rnb", "Tyler, The Creator|SZA|Daniel Caesar|Steve Lacy|Blood Orange",
                    [("Pink + White", 2016), ("Nights", 2016), ("Thinkin Bout You", 2012), ("Ivy", 2016)]),
    "Radiohead": ("alternative rock,rock,electronic", "Thom Yorke|The Smile|Portishead|Muse|Arcade Fire",
                  [("Weird Fishes/Arpeggi", 2007), ("Karma Police", 1997), ("No Surprises", 1997), ("Creep", 1992)]),
    "Fleetwood Mac": ("classic rock,rock,soft rock", "Stevie Nicks|The Eagles|Heart|Carole King",
                      [("Dreams", 1977), ("The Chain", 1977), ("Everywhere", 1987), ("Landslide", 1975)]),
    "The Beatles": ("classic rock,rock,60s", "The Beach Boys|The Kinks|The Rolling Stones|Simon & Garfunkel",
                    [("Here Comes the Sun", 1969), ("Something", 1969), ("Let It Be", 1970)]),
    "Chappell Roan": ("pop,synthpop,indie pop", "Olivia Rodrigo|Reneé Rapp|Gracie Abrams|Sabrina Carpenter|Muna",
                      [("Pink Pony Club", 2020), ("HOT TO GO!", 2023), ("Good Luck, Babe!", 2024)]),
}
SIMILAR_EXTRA = {  # artists only known via similarity, so recs have somewhere to go
    "Julien Baker": "indie folk,singer-songwriter", "Lucy Dacus": "indie rock,indie", "Mitski": "indie rock,indie",
    "Big Thief": "indie folk,folk", "Steve Lacy": "rnb,alternative rnb", "Daniel Caesar": "rnb,soul",
    "Baby Keem": "hip-hop,rap", "Taking Back Sunday": "emo,pop punk", "The Smile": "alternative rock,rock",
    "Olivia Rodrigo": "pop,pop rock", "Sufjan Stevens": "indie folk,folk", "Vince Staples": "hip-hop,rap",
}

# monthly weight per artist group by year (you), shaping eras and ghosts
YOU = {
    2022: {"Phoebe Bridgers": 9, "Bon Iver": 7, "Fleet Foxes": 6, "Brand New": 3, "Radiohead": 4, "Fleetwood Mac": 2, "The Beatles": 2},
    2023: {"My Chemical Romance": 9, "Paramore": 8, "Fall Out Boy": 6, "Brand New": 6, "Phoebe Bridgers": 3, "Radiohead": 4, "Fleetwood Mac": 2},
    2024: {"Kendrick Lamar": 9, "SZA": 7, "Tyler, The Creator": 6, "Frank Ocean": 5, "Paramore": 2, "Radiohead": 3, "Chappell Roan": 4},
    2025: {"Kendrick Lamar": 6, "Tyler, The Creator": 7, "SZA": 5, "Chappell Roan": 6, "Radiohead": 3, "Fleetwood Mac": 3},
}
FRIEND = {
    2022: {"Kendrick Lamar": 6, "Frank Ocean": 8, "The Beatles": 4, "Fleetwood Mac": 5},
    2023: {"SZA": 9, "Frank Ocean": 6, "Paramore": 4, "Kendrick Lamar": 5},
    2024: {"SZA": 8, "Chappell Roan": 9, "Tyler, The Creator": 4, "Radiohead": 2},
    2025: {"Chappell Roan": 9, "SZA": 5, "Fleetwood Mac": 4},
}
# Tracks you skip constantly but keep in playlists
SKIPPY = {("Thnks fr th Mmrs", "Fall Out Boy"), ("Creep", "Radiohead"), ("Let It Be", "The Beatles")}


def uri(track, artist):
    return "spotify:track:" + hashlib.md5(f"{artist}|{track}".encode()).hexdigest()[:22]


def track_length(track, artist):
    return 170_000 + int(hashlib.md5(f"{track}{artist}".encode()).hexdigest(), 16) % 120_000


DEEP_CUTS = [("Fleetwood Mac", "Songbird"), ("Radiohead", "True Love Waits"), ("Frank Ocean", "Godspeed"),
             ("Phoebe Bridgers", "Funeral"), ("SZA", "Special")]


def gen_plays(weights, rng, end=datetime(2025, 10, 28)):
    rows, day = [], datetime(2022, 1, 1)
    while day <= end:
        year_w = weights.get(day.year, {})
        artists, w = list(year_w), list(year_w.values())
        for _ in range(rng.randint(8, 40) if rng.random() > 0.1 else 0):
            artist = rng.choices(artists, w)[0]
            track, _ = rng.choice(CATALOG[artist][2])
            hour = rng.choices(range(24), [1, 1, 0, 0, 0, 0, 1, 3, 5, 4, 3, 3, 4, 4, 3, 4, 5, 6, 6, 7, 8, 8, 6, 3])[0]
            ts = day + timedelta(hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
            length = track_length(track, artist)
            skip = rng.random() < (0.7 if (track, artist) in SKIPPY else 0.08)
            ms = rng.randint(6_000, 25_000) if skip else length
            rows.append({
                "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "platform": "android", "ms_played": ms,
                "master_metadata_track_name": track, "master_metadata_album_artist_name": artist,
                "master_metadata_album_album_name": f"{track} (Album)",
                "spotify_track_uri": uri(track, artist),
                "reason_start": "trackdone", "reason_end": "fwdbtn" if skip else "trackdone",
                "shuffle": rng.random() < 0.5, "skipped": skip,
            })
        if rng.random() < 0.012:  # rare deep cuts, always played to the end
            artist, track = rng.choice(DEEP_CUTS)
            ts = day + timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
            rows.append({"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "platform": "android",
                         "ms_played": track_length(track, artist), "master_metadata_track_name": track,
                         "master_metadata_album_artist_name": artist, "master_metadata_album_album_name": f"{track} (Album)",
                         "spotify_track_uri": uri(track, artist), "reason_start": "clickrow", "reason_end": "trackdone",
                         "shuffle": False, "skipped": False})
        day += timedelta(days=1)
    return rows


def gen_playlists(rng):
    def item(artist, track):
        return {"track": {"trackName": track, "artistName": artist, "albumName": f"{track} (Album)",
                          "trackUri": uri(track, artist)}}
    lists = {
        "keep forever": list(DEEP_CUTS),
        "sad girl autumn": [(a, t) for a in ("Phoebe Bridgers", "Bon Iver", "Fleet Foxes") for t, _ in CATALOG[a][2]],
        "2023 emo revival": [(a, t) for a in ("My Chemical Romance", "Paramore", "Fall Out Boy", "Brand New") for t, _ in CATALOG[a][2]],
        "daily drive": [("Kendrick Lamar", "Not Like Us"), ("SZA", "Kill Bill"), ("Radiohead", "Creep"),
                        ("Tyler, The Creator", "EARFQUAKE"), ("The Beatles", "Let It Be"), ("Chappell Roan", "HOT TO GO!")],
    }
    return {"playlists": [{"name": n, "items": [item(a, t) for a, t in tracks]} for n, tracks in lists.items()]}


def seed_db():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from db import ArtistInfo, SessionLocal, TrackInfo, init_db
    init_db()
    with SessionLocal() as s:
        for artist, (genres, similar, tracks) in CATALOG.items():
            s.merge(ArtistInfo(artist=artist, genres=genres, similar=similar, looked_up=True))
            for t, y in tracks:
                s.merge(TrackInfo(key=f"{artist}\t{t}", release_year=y, looked_up=True))
        for artist, genres in SIMILAR_EXTRA.items():
            s.merge(ArtistInfo(artist=artist, genres=genres, similar="", looked_up=True))
        s.commit()
    print("Seeded metadata cache.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--friend", action="store_true")
    ap.add_argument("--seed-db", action="store_true")
    args = ap.parse_args()
    rng = random.Random(7 if args.friend else 42)
    plays = gen_plays(FRIEND if args.friend else YOU, rng)
    out = Path("friend_export.zip" if args.friend else "sample_export.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        half = len(plays) // 2
        zf.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2022-2023_0.json", json.dumps(plays[:half]))
        zf.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2024-2025_1.json", json.dumps(plays[half:]))
        if not args.friend:
            zf.writestr("Spotify Account Data/Playlist1.json", json.dumps(gen_playlists(rng)))
    print(f"Wrote {out} ({len(plays):,} plays)")
    if args.seed_db:
        seed_db()
