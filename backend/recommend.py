"""Recommendations and the AI autobiography summary.

Candidates always come from real similarity data (Last.fm similar artists, cached in
artist_info). The LLM may only reorder and explain that list; anything it returns that
isn't in the list is dropped. With Ollama off, plain similarity scores are used.
"""
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select

import enrich
import stats
from db import ArtistInfo, Play


def candidates(s, pid, n=15):
    end = stats.last_played(s, pid)
    known = {a.lower() for (a,) in s.execute(select(Play.artist).where(Play.profile_id == pid).distinct())}
    seeds = s.execute(select(Play.artist, func.count()).where(
        Play.profile_id == pid, Play.played_at >= end - timedelta(days=stats.RECENT_DAYS))
        .group_by(Play.artist).order_by(func.count().desc()).limit(12)).all()
    score, because = Counter(), defaultdict(list)
    for rank, (seed, _) in enumerate(seeds):
        info = s.get(ArtistInfo, seed)
        similar = info.similar.split("|") if info and info.similar else []
        for i, cand in enumerate(similar[:15]):
            if cand and cand.lower() not in known:
                score[cand] += (12 - rank) * (15 - i)
                because[cand].append(seed)
    return [{"artist": a, "score": sc, "because": because[a][:3]} for a, sc in score.most_common(n)]


def recommend(s, pid, use_ai=True):
    cands = candidates(s, pid)
    tops = enrich.top_tracks_many(s, [c["artist"] for c in cands])
    for c in cands:
        c["tracks"] = tops[c["artist"]][:3]
        c["why"] = f"Fans of {' and '.join(c['because'][:2])} play them a lot."
    if not cands or not use_ai:
        return {"items": cands, "ai": False}

    top_genres = stats.genres(s, pid, stats.Filters())["genres"][:6]
    listing = "\n".join(f"{i}. {c['artist']} (similar to {', '.join(c['because'])})" for i, c in enumerate(cands))
    prompt = (
        "You are a music recommender. The listener's top genres: "
        f"{', '.join(g['genre'] for g in top_genres)}.\n"
        f"Candidate artists (you may ONLY choose from these, by number):\n{listing}\n\n"
        'Pick the best 8 for this listener. Reply as JSON: {"picks": [{"index": <number>, '
        '"why": "<one short, specific sentence>"}]}'
    )
    out = enrich.ollama_json(prompt)
    picks = out.get("picks") if isinstance(out, dict) else None
    if not isinstance(picks, list):
        return {"items": cands, "ai": False}
    ranked, seen = [], set()
    for p in picks:
        i = p.get("index") if isinstance(p, dict) else None
        if isinstance(i, int) and 0 <= i < len(cands) and i not in seen:  # reject anything invented
            seen.add(i)
            ranked.append({**cands[i], "why": str(p.get("why") or cands[i]["why"])[:240]})
    ranked += [c for i, c in enumerate(cands) if i not in seen]
    return {"items": ranked, "ai": bool(seen)}


def summary(s, pid):
    f = stats.Filters()
    ov = stats.overview(s, pid, f)
    era_list = stats.eras(s, pid, f)
    ghost_list = stats.ghosts(s, pid, f, limit=5)
    top = stats.top_artists(s, pid, f, limit=5)
    facts = {
        "hours": round(ov["minutes"] / 60), "span": f"{ov['first'][:10]} to {ov['last'][:10]}",
        "top_artists": [a["artist"] for a in top],
        "eras": [e["headline"] for e in era_list],
        "ghosts": [g["artist"] for g in ghost_list],
    }
    text = enrich.ollama(
        "Write a short, warm, slightly funny 'liner notes' paragraph (90-130 words, second person) "
        f"about this person's music history. Use only these facts, invent nothing:\n{facts}")
    if text:
        return {"text": text.strip(), "ai": True}
    arc = []
    for e in era_list:
        g = e["genre"] or e["artist"]
        if not arc or arc[-1] != g:
            arc.append(g)
    eras_txt = ", then ".join(arc[-4:])
    ghost_txt = f" Somewhere along the way you ghosted {', '.join(facts['ghosts'][:3])}." if facts["ghosts"] else ""
    return {"ai": False, "text": (
        f"{facts['hours']:,} hours of music, {facts['span']}. "
        f"The constant: {', '.join(facts['top_artists'][:3])}. "
        f"Your eras went {eras_txt}.{ghost_txt}")}


def _template_note(t):
    months = max(1, round(t["days_gone"] / 30))
    flavor = f"{t['genre']} " if t["genre"] else ""
    return (f"From your {t['era']} {flavor}era: {t['plays']} plays and you almost never skipped it. "
            f"Last heard {months} month{'s' if months != 1 else ''} ago.")


def mixtape(s, pid, length=15, months_gone=6, use_ai=True, fresh=3):
    """A Rediscovery Mixtape: forgotten favourites in story order, each with a liner note.

    Like `recommend`, the LLM may only reorder and annotate the fixed candidate list; anything else is dropped.
    """
    tracks = stats.rediscoveries(s, pid, months_gone, limit=length)
    tracks.sort(key=lambda t: t["peak_month"])  # default order: oldest era first, a walk through time
    for t in tracks:
        t["note"] = _template_note(t)
    result = {"title": f"Rediscovery, {len(tracks)} tracks", "tracks": tracks, "ai": False,
              "fresh": _fresh_picks(s, pid, tracks, fresh)}
    if not tracks or not use_ai:
        return result

    listing = "\n".join(f"{i}. \"{t['track']}\" by {t['artist']} (peaked {t['era']}, {t['plays']} plays, "
                        f"last played {t['days_gone']} days ago)" for i, t in enumerate(tracks))
    out = enrich.ollama_json(
        "You are making a mixtape of songs this listener loved but forgot. Use ONLY these tracks, by number:\n"
        f"{listing}\n\nOrder ALL of them so the tape flows well, and write a short, warm liner note (max 20 words) "
        "for each using only the facts given. Also give the tape a short title. Reply as JSON: "
        '{"title": "<title>", "order": [{"index": <number>, "note": "<note>"}]}')
    order = out.get("order") if isinstance(out, dict) else None
    if not isinstance(order, list):
        return result
    ranked, seen = [], set()
    for o in order:
        i = o.get("index") if isinstance(o, dict) else None
        if isinstance(i, int) and 0 <= i < len(tracks) and i not in seen:  # reject anything invented
            seen.add(i)
            ranked.append({**tracks[i], "note": str(o.get("note") or tracks[i]["note"])[:200]})
    if not seen:
        return result
    ranked += [t for i, t in enumerate(tracks) if i not in seen]
    title = out.get("title")
    return {**result, "tracks": ranked, "ai": True,
            "title": str(title)[:120] if isinstance(title, str) and title.strip() else result["title"]}


def _fresh_picks(s, pid, tracks, n):
    """A few new songs from artists similar to the ones on the tape (needs Last.fm; empty otherwise)."""
    if not n:
        return []
    known = {a.lower() for (a,) in s.execute(select(Play.artist).where(Play.profile_id == pid).distinct())}
    infos = {i.artist: i for i in s.scalars(select(ArtistInfo).where(ArtistInfo.artist.in_({t["artist"] for t in tracks})))}
    picks, used = [], set()  # (similar artist, the tape artist it's similar to)
    for t in tracks:
        info = infos.get(t["artist"])
        for cand in (info.similar.split("|") if info and info.similar else [])[:5]:
            if cand and cand.lower() not in known and cand not in used:
                used.add(cand)
                picks.append((cand, t["artist"]))
                break
        if len(picks) >= n * 2:  # a few spares in case Last.fm has nothing for some
            break
    tops = enrich.top_tracks_many(s, [c for c, _ in picks])
    return [{"track": tops[c][0], "artist": c, "because": b} for c, b in picks if tops[c]][:n]
