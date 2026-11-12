"""Email/password accounts. Logging in returns the profile's existing secret key, so the rest of the API is unchanged.

Email addresses are not verified yet. Password resets create a one-time link that expires after an hour; until the app
can send email, the link is printed to the server log (see `send_reset_link`).
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from db import Account, Profile, SessionLocal

log = logging.getLogger("linernotes.auth")
router = APIRouter(prefix="/api/auth")

MIN_PASSWORD = 8
RESET_TTL = timedelta(hours=1)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def db():
    with SessionLocal() as s:
        yield s


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **SCRYPT)
    return hmac.compare_digest(test.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, "Enter a valid email address.")
    return email


def _password(raw: str) -> str:
    if len(raw) < MIN_PASSWORD:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters.")
    return raw


def send_reset_link(email: str, link: str):
    """Placeholder until email sending exists: log the link so it can be copied from the server console."""
    log.warning("Password reset link for %s: %s", email, link)
    print(f"[liner notes] Password reset link for {email}: {link}", flush=True)


class SignupIn(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


@router.post("/signup")
def signup(body: SignupIn, s=Depends(db)):
    email, password = _email(body.email), _password(body.password)
    if s.scalar(select(Account).where(Account.email == email)):
        raise HTTPException(409, "An account with that email already exists. Try logging in.")
    p = Profile(name=body.name.strip()[:80] or email.split("@")[0][:80], secret=secrets.token_urlsafe(24))
    s.add(p)
    s.flush()
    s.add(Account(email=email, password_hash=hash_password(password), profile_id=p.id))
    s.commit()
    return {"key": p.secret, "name": p.name, "email": email}


@router.post("/login")
def login(body: LoginIn, s=Depends(db)):
    a = s.scalar(select(Account).where(Account.email == body.email.strip().lower()))
    if not a or not check_password(body.password, a.password_hash):
        raise HTTPException(401, "Wrong email or password.")
    p = s.get(Profile, a.profile_id)
    return {"key": p.secret, "name": p.name, "email": a.email}


@router.post("/forgot")
def forgot(body: ForgotIn, request: Request, s=Depends(db)):
    # Same answer whether or not the account exists, so this can't be used to find out who has an account.
    a = s.scalar(select(Account).where(Account.email == body.email.strip().lower()))
    if a:
        token = secrets.token_urlsafe(32)
        a.reset_token_hash, a.reset_expires = _token_hash(token), datetime.utcnow() + RESET_TTL
        s.commit()
        base = os.getenv("PUBLIC_URL", str(request.base_url)).rstrip("/")
        send_reset_link(a.email, f"{base}/?reset={token}")
    return {"ok": True}


@router.post("/reset")
def reset(body: ResetIn, s=Depends(db)):
    password = _password(body.password)
    a = s.scalar(select(Account).where(Account.reset_token_hash == _token_hash(body.token)))
    if not a or not a.reset_expires or a.reset_expires < datetime.utcnow():
        raise HTTPException(400, "That reset link is invalid or has expired. Ask for a new one.")
    a.password_hash = hash_password(password)
    a.reset_token_hash = a.reset_expires = None
    s.commit()
    p = s.get(Profile, a.profile_id)
    return {"key": p.secret, "name": p.name, "email": a.email}
