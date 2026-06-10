import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email      TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                email         TEXT PRIMARY KEY,
                age_group     TEXT DEFAULT '18-64歲',
                conditions    TEXT DEFAULT '',
                other_notes   TEXT DEFAULT '',
                fav_locations TEXT DEFAULT '[]',
                updated_at    TEXT NOT NULL,
                FOREIGN KEY (email) REFERENCES users(email)
            )
        """)
        conn.commit()
    print("✅ users DB 初始化完成")


def upsert_user(email: str) -> dict:
    now = datetime.now().isoformat()
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO users (email, created_at)
            VALUES (?, ?)
            ON CONFLICT(email) DO NOTHING
        """, (email, now))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row)


def get_user_profile(email: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE email = ?", (email,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["fav_locations"] = json.loads(d.get("fav_locations") or "[]")
    return d


def upsert_user_profile(
    email: str,
    age_group: str,
    conditions: str,
    other_notes: str,
    fav_locations: list,
) -> dict:
    now = datetime.now().isoformat()
    fav_json = json.dumps(fav_locations, ensure_ascii=False)
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO user_profiles (email, age_group, conditions, other_notes, fav_locations, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                age_group     = excluded.age_group,
                conditions    = excluded.conditions,
                other_notes   = excluded.other_notes,
                fav_locations = excluded.fav_locations,
                updated_at    = excluded.updated_at
        """, (email, age_group, conditions, other_notes, fav_json, now))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE email = ?", (email,)
        ).fetchone()
    d = dict(row)
    d["fav_locations"] = json.loads(d.get("fav_locations") or "[]")
    return d
