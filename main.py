from __future__ import annotations

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from passlib.context import CryptContext
import sqlite3
import os
import uuid
import json
import jwt

# ============================================================
# App
# ============================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://instagram-leads-frontend.vercel.app",
        "https://instagram-leads-frontend-2cfwxkfi5-monidipa-khans-projects.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Storage (SQLite) — FIXED FOR RENDER
# ============================================================

DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/render/project/data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "data.db"

ALLOWED_STATUS = {"PENDING", "CHAT_OPENED", "SENT"}

# ============================================================
# Security / Auth
# ============================================================

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-prod")
JWT_ALG = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("ACCESS_TOKEN_EXPIRE_DAYS", "7"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=True)

WORKER_KEY = os.getenv("WORKER_KEY", "")  # required for worker-only routes if set

# ============================================================
# Models
# ============================================================

class SearchRequest(BaseModel):
    keywords: List[str] = Field(default_factory=list)

class LeadOut(BaseModel):
    username: str
    bio: str = ""
    matched_keyword: Optional[str] = None

class LeadStored(BaseModel):
    username: str
    bio: str = ""
    status: str = "PENDING"

class StatusUpdate(BaseModel):
    username: str
    status: str

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

# ============================================================
# DB Helpers
# ============================================================

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                username TEXT PRIMARY KEY,
                bio TEXT DEFAULT '',
                status TEXT DEFAULT 'PENDING'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_leads (
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                bio TEXT DEFAULT '',
                profile_url TEXT,
                followers_count INTEGER,
                is_private INTEGER,
                matched_keywords TEXT,
                match_sources TEXT,
                last_updated TEXT NOT NULL,
                PRIMARY KEY (user_id, username)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_jobs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                keywords TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

init_db()

# ============================================================
# Utility Helpers
# ============================================================

def _normalize_keywords(keywords: List[str]) -> List[str]:
    return [k.strip() for k in keywords if k and k.strip()][:100]

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

def _get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    try:
        data = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALG])
        uid = data.get("sub")
        if not uid:
            raise HTTPException(status_code=401, detail="invalid token")
        return uid
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")

def _require_worker(x_worker_key: Optional[str] = Header(default=None, alias="X-Worker-Key")):
    if WORKER_KEY and x_worker_key != WORKER_KEY:
        raise HTTPException(status_code=401, detail="worker unauthorized")

# ============================================================
# Routes
# ============================================================

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/auth/signup")
def signup(payload: SignupRequest):
    email = payload.email.strip().lower()
    password = payload.password

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
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ?",
            (payload.email.strip().lower(),),
        ).fetchone()

    if not row or not _verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = _create_access_token({"sub": row["id"]})
    return TokenResponse(access_token=token, user_id=row["id"])

@app.post("/scrape-request")
def create_scrape_request(
    req: ScrapeJobCreate, user_id: str = Depends(_get_current_user_id)
):
    keywords = _normalize_keywords(req.keywords)
    if not keywords:
        raise HTTPException(status_code=400, detail="keywords required")

    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scrape_jobs VALUES (?, ?, ?, 'PENDING', ?, ?)",
            (job_id, user_id, json.dumps(keywords), now, now),
        )
        conn.commit()

    return {"job_id": job_id, "status": "PENDING"}

@app.get("/scrape-jobs/pending", response_model=Optional[WorkerJobOut])
def get_pending_job(_: None = Depends(_require_worker)):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, user_id, keywords FROM scrape_jobs WHERE status='PENDING' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None

        conn.execute(
            "UPDATE scrape_jobs SET status='IN_PROGRESS', updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), row["id"]),
        )
        conn.execute("COMMIT")

    return WorkerJobOut(
        job_id=row["id"],
        user_id=row["user_id"],
        keywords=json.loads(row["keywords"]),
    )

@app.post("/scrape-results")
def post_scrape_results(payload: ScrapeResultsIn, _: None = Depends(_require_worker)):
    now = datetime.utcnow().isoformat()

    with get_conn() as conn:
        for lead in payload.leads:
            conn.execute(
                """
                INSERT INTO user_leads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, username) DO UPDATE SET
                    bio=excluded.bio,
                    profile_url=excluded.profile_url,
                    followers_count=excluded.followers_count,
                    is_private=excluded.is_private,
                    matched_keywords=excluded.matched_keywords,
                    match_sources=excluded.match_sources,
                    last_updated=excluded.last_updated
                """,
                (
                    payload.user_id,
                    lead.username,
                    lead.bio or "",
                    lead.profile_url,
                    lead.followers_count,
                    1 if lead.is_private else 0 if lead.is_private is not None else None,
                    json.dumps(lead.matched_keywords or []),
                    json.dumps(lead.match_sources or []),
                    now,
                ),
            )

        conn.execute(
            "UPDATE scrape_jobs SET status='COMPLETED', updated_at=? WHERE id=?",
            (now, payload.job_id),
        )
        conn.commit()

    return {"ok": True}

@app.get("/me/leads", response_model=List[LeadStored])
def get_my_leads(user_id: str = Depends(_get_current_user_id)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT username, bio FROM user_leads WHERE user_id=? ORDER BY username",
            (user_id,),
        ).fetchall()

    return [LeadStored(username=r["username"], bio=r["bio"], status="PENDING") for r in rows]
