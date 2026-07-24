"""JWT authentication module for BCS Batighor API."""

import os
import uuid
import sqlite3
import datetime
from typing import Optional, Dict, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
import bcrypt
from dotenv import load_dotenv

load_dotenv()

from bcs.logging_config import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SECRET_KEY = os.getenv("JWT_SECRET", "")
if not SECRET_KEY:
    SECRET_KEY = uuid.uuid4().hex
    log.warning("JWT_SECRET not set — using ephemeral key (tokens invalid on restart).")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

USER_DB_PATH = os.getenv("USER_DB_PATH", "runtime/users.db")

security = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# User storage (SQLite)
# ---------------------------------------------------------------------------

def _get_user_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(USER_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      TEXT PRIMARY KEY,
            email        TEXT UNIQUE NOT NULL,
            hashed_pass  TEXT NOT NULL,
            display_name TEXT,
            created_at   TEXT NOT NULL,
            is_active    INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def create_user(email: str, password: str, display_name: Optional[str] = None) -> Dict[str, Any]:
    conn = _get_user_conn()
    try:
        user_id = f"USER_{uuid.uuid4().hex[:12]}"
        now = datetime.datetime.now().isoformat()
        hashed = hash_password(password)
        conn.execute("""
            INSERT INTO users (user_id, email, hashed_pass, display_name, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (user_id, email, hashed, display_name, now))
        conn.commit()
        return {"user_id": user_id, "email": email, "display_name": display_name, "created_at": now}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Email already registered")
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    conn = _get_user_conn()
    try:
        cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_user_conn()
    try:
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(data: Dict[str, Any], expires_delta: Optional[datetime.timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.timezone.utc) + (expires_delta or datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_access_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[Dict[str, Any]]:
    """Extract current user from Bearer token. Returns None if no token."""
    if credentials is None:
        return None
    payload = verify_access_token(credentials.credentials)
    user = get_user_by_id(payload.get("sub", ""))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.get("is_active"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deactivated")
    return user


async def require_user(
    user: Optional[Dict[str, Any]] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Like get_current_user but raises 401 if no token."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
