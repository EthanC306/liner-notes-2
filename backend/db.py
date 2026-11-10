"""Database setup. PostgreSQL in production; SQLite fallback for quick local runs."""
import os
from pathlib import Path
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{Path(__file__).resolve().parent / 'linernotes.db'}")

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
Base = declarative_base()


class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    secret = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    has_extended = Column(Boolean, default=False)  # extended history has skips


class Play(Base):
    __tablename__ = "plays"
    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    played_at = Column(DateTime, nullable=False)
    track = Column(String(300), nullable=False)
    artist = Column(String(300), nullable=False, index=True)
    album = Column(String(300))
    uri = Column(String(64))
    ms_played = Column(Integer, nullable=False)
    skipped = Column(Boolean)  # None when unknown (basic export)
    # Precomputed so stats SQL is identical on SQLite and Postgres.
    year = Column(Integer, index=True)
    month = Column(String(7))  # YYYY-MM
    hour = Column(Integer)
    weekday = Column(Integer)  # 0 = Monday
    __table_args__ = (UniqueConstraint("profile_id", "played_at", "track", "artist", name="uq_play"),)


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    playlist = Column(String(300), nullable=False)
    track = Column(String(300), nullable=False)
    artist = Column(String(300), nullable=False)
    album = Column(String(300))
    uri = Column(String(64))


class ArtistInfo(Base):
    """Shared cache: each artist is looked up once across all users."""
    __tablename__ = "artist_info"
    artist = Column(String(300), primary_key=True)
    genres = Column(Text, default="")  # comma-separated, best first
    similar = Column(Text, default="")
    looked_up = Column(Boolean, default=False)


class ArtistTopTracks(Base):
    """Shared cache of Last.fm top tracks, so recommendations don't re-ask Last.fm on every page load."""
    __tablename__ = "artist_top_tracks"
    artist = Column(String(300), primary_key=True)
    tracks = Column(Text, default="")  # "|"-separated, best first; "" = Last.fm has none


class TrackInfo(Base):
    __tablename__ = "track_info"
    key = Column(String(620), primary_key=True)  # "artist\ttrack"
    release_year = Column(Integer)
    looked_up = Column(Boolean, default=False)


class LocalTag(Base):
    __tablename__ = "local_tags"
    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    artist = Column(String(300), nullable=False)
    city = Column(String(120), nullable=False)


class Mixtape(Base):
    """A saved Rediscovery Mixtape. `slug` is a public share link; it reveals only the tracklist."""
    __tablename__ = "mixtapes"
    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    slug = Column(String(32), unique=True, nullable=False, index=True)
    title = Column(String(120), nullable=False)
    tracks = Column(Text, nullable=False)  # JSON list of {track, artist, uri, era, note}
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)
