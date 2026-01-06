from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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
