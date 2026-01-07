from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timedelta
from passlib.context import CryptContext
import jwt
import uuid
import json

# ---------- Models ----------
class SearchRequest(BaseModel):
    keywords: List[str] = Field(default_factory=list)

class LeadOut(BaseModel):
    username: str
    bio: str = ""
    matched_keyword: Optional[str] = None

class LeadStored(BaseModel):
    username: str
    bio: str = ""
    status: str = "PENDING"  # PENDING | CHAT_OPENED | SENT

class StatusUpdate(BaseModel):
    username: str
    status: str  # PENDING | CHAT_OPENED | SENT


# ---------- New Auth & Job Models (Additive) ----------
class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    user_id: str


class ScrapeJobCreate(BaseModel):
    keywords: List[str] = Field(default_factory=list)


class WorkerJobOut(BaseModel):
    job_id: str
    user_id: str
    keywords: List[str]


class LeadIn(BaseModel):
    username: str
    bio: str = ""
    profile_url: Optional[str] = None
    followers_count: Optional[int] = None
    is_private: Optional[bool] = None
    matched_keywords: Optional[List[str]] = None
    match_sources: Optional[List[str]] = None


class ScrapeResultsIn(BaseModel):
    job_id: str
    user_id: str
    leads: List[LeadIn] = Field(default_factory=list)


# ---------- App ----------
app = FastAPI(title="Leads API (API-only)")

# CORS for Vercel frontend
_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "*")
origins = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ---------- Storage (SQLite) ----------
DATA_DIR = Path(os.getenv("DATA_DIR", ".")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "data.db"

ALLOWED_STATUS = {"PENDING", "CHAT_OPENED", "SENT"}

# ---------- Auth / Security ----------
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-prod")
JWT_ALG = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("ACCESS_TOKEN_EXPIRE_DAYS", "7"))
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=True)
WORKER_KEY = os.getenv("WORKER_KEY", "")  # If set, required by worker-only endpoints


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                username TEXT PRIMARY KEY,
                bio TEXT DEFAULT '',
                status TEXT DEFAULT 'PENDING'
            )
            """
        )
        # Users table (separate, additive)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Per-user leads (private)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_leads (
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                bio TEXT DEFAULT '',
                profile_url TEXT,
                followers_count INTEGER,
                is_private INTEGER,
                matched_keywords TEXT, -- JSON list
                match_sources TEXT,    -- JSON list
                last_updated TEXT NOT NULL,
                PRIMARY KEY (user_id, username)
            )
            """
        )
        # Scrape jobs (owner PC worker processes these)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scrape_jobs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                keywords TEXT NOT NULL, -- JSON list
                status TEXT NOT NULL,   -- PENDING | IN_PROGRESS | COMPLETED
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


init_db()


# ---------- Helpers ----------

def _normalize_keywords(keywords: List[str]) -> List[str]:
    ks = []
    for k in keywords or []:
        k = (k or "").strip()
        if not k:
            continue
        ks.append(k)
    # limit to reasonable size
    return ks[:100]


def _hash_password(pw: str) -> str:
    return pwd_context.hash(pw)


def _verify_password(pw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(pw, hashed)
    except Exception:
        return False


def _create_access_token(subject: Dict[str, Any]) -> str:
    exp = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {**subject, "exp": exp}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    token = credentials.credentials
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        uid = data.get("sub") or data.get("user_id")
        if not uid:
            raise HTTPException(status_code=401, detail="invalid token")
        return str(uid)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")


def _require_worker(x_worker_key: Optional[str] = Header(default=None, alias="X-Worker-Key")) -> None:
    # If WORKER_KEY is set on server, require exact match; otherwise allow for development
    if WORKER_KEY and x_worker_key != WORKER_KEY:
        raise HTTPException(status_code=401, detail="worker unauthorized")


# ---------- Endpoints ----------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/search", response_model=List[LeadOut])
def search(req: SearchRequest):
    keywords = _normalize_keywords(req.keywords)
    if not keywords:
        return []
    # Fetch all leads and filter in app layer to compute matched_keyword
    with get_conn() as conn:
        rows = conn.execute("SELECT username, bio FROM leads").fetchall()
    out: List[LeadOut] = []
    for row in rows:
        username = row["username"] or ""
        bio = row["bio"] or ""
        text = f"{username}\n{bio}".lower()
        mk = None
        for k in keywords:
            if k.lower() in text:
                mk = k
                break
        if mk is not None:
            out.append(LeadOut(username=username, bio=bio, matched_keyword=mk))
    return out


@app.get("/leads", response_model=List[LeadStored])
def get_leads():
    with get_conn() as conn:
        rows = conn.execute("SELECT username, bio, status FROM leads ORDER BY username COLLATE NOCASE").fetchall()
    return [LeadStored(username=r["username"], bio=r["bio"], status=r["status"]) for r in rows]


@app.post("/status")
def set_status(payload: StatusUpdate):
    username = (payload.username or "").strip()
    status = (payload.status or "").strip().upper()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    if status not in ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="invalid status")
    with get_conn() as conn:
        # Upsert: update if exists, else insert with empty bio
        cur = conn.execute("SELECT 1 FROM leads WHERE username = ?", (username,))
        if cur.fetchone():
            conn.execute("UPDATE leads SET status = ? WHERE username = ?", (status, username))
        else:
            conn.execute("INSERT INTO leads (username, bio, status) VALUES (?, ?, ?)", (username, "", status))
        conn.commit()
    return {"ok": True, "username": username, "status": status}


# ---------- New Additive Endpoints ----------

@app.post("/auth/signup")
def signup(payload: SignupRequest):
    email = (payload.email or "").strip().lower()
    password = payload.password or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="email and password required")
    user_id = str(uuid.uuid4())
    pw_hash = _hash_password(password)
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, email, pw_hash, datetime.utcnow().isoformat()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="email already exists")
    return {"ok": True, "user_id": user_id}


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    email = (payload.email or "").strip().lower()
    password = payload.password or ""
    with get_conn() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    user_id = row["id"]
    token = _create_access_token({"sub": user_id})
    return TokenResponse(access_token=token, user_id=user_id)


@app.post("/scrape-request")
def create_scrape_request(req: ScrapeJobCreate, user_id: str = Depends(_get_current_user_id)):
    keywords = _normalize_keywords(req.keywords)
    if not keywords:
        raise HTTPException(status_code=400, detail="keywords required")
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scrape_jobs (id, user_id, keywords, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, user_id, json.dumps(keywords), "PENDING", now, now),
        )
        conn.commit()
    return {"job_id": job_id, "status": "PENDING"}


@app.get("/scrape-jobs/pending", response_model=Optional[WorkerJobOut])
def get_pending_job(_: None = Depends(_require_worker)):
    # Atomically claim a single pending job
    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, user_id, keywords FROM scrape_jobs WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            job_id = row["id"]
            conn.execute(
                "UPDATE scrape_jobs SET status = 'IN_PROGRESS', updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), job_id),
            )
            conn.execute("COMMIT")
            return WorkerJobOut(job_id=job_id, user_id=row["user_id"], keywords=json.loads(row["keywords"]))
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


@app.post("/scrape-results")
def post_scrape_results(payload: ScrapeResultsIn, _: None = Depends(_require_worker)):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        # Upsert leads for this user
        for lead in payload.leads:
            matched_keywords_json = json.dumps(lead.matched_keywords or [])
            match_sources_json = json.dumps(lead.match_sources or [])
            conn.execute(
                """
                INSERT INTO user_leads (user_id, username, bio, profile_url, followers_count, is_private, matched_keywords, match_sources, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, username) DO UPDATE SET
                    bio = excluded.bio,
                    profile_url = excluded.profile_url,
                    followers_count = excluded.followers_count,
                    is_private = excluded.is_private,
                    matched_keywords = excluded.matched_keywords,
                    match_sources = excluded.match_sources,
                    last_updated = excluded.last_updated
                """,
                (
                    payload.user_id,
                    lead.username,
                    lead.bio or "",
                    lead.profile_url,
                    lead.followers_count,
                    1 if (lead.is_private is True) else 0 if (lead.is_private is False) else None,
                    matched_keywords_json,
                    match_sources_json,
                    now,
                ),
            )
        # Mark job completed
        conn.execute(
            "UPDATE scrape_jobs SET status='COMPLETED', updated_at=? WHERE id = ?",
            (now, payload.job_id),
        )
        conn.commit()
    return {"ok": True}


@app.get("/me/leads", response_model=List[LeadStored])
def get_my_leads(user_id: str = Depends(_get_current_user_id)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT username, bio FROM user_leads WHERE user_id = ? ORDER BY username COLLATE NOCASE",
            (user_id,),
        ).fetchall()
    # New per-user leads don't track DM status; default to PENDING for compatibility with UI
    return [LeadStored(username=r["username"], bio=r["bio"], status="PENDING") for r in rows]
