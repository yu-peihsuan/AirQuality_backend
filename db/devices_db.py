# db/devices_db.py
# 已註冊裝置的 SQLite 資料表（供 refresh 驗證與封鎖濫用裝置使用）

import sqlite3
import os
from datetime import datetime

# 與 user_reports.db 放在同一位置，沿用既有的資料目錄慣例
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "crawler", "user_reports.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_device_db():
    """建立 devices 資料表（冪等，可重複呼叫）。"""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id  TEXT PRIMARY KEY,
                created    TEXT,
                last_seen  TEXT,
                revoked    INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    print("✅ devices DB 初始化完成")


def upsert_device(device_id: str):
    """註冊裝置；已存在則只更新 last_seen（不影響 revoked 狀態）。"""
    now = datetime.now().isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO devices (device_id, created, last_seen, revoked)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen
            """,
            (device_id, now, now),
        )
        conn.commit()


def is_active(device_id: str) -> bool:
    """裝置是否存在且未被封鎖。

    注意：Cloud Run 的容器檔案系統是暫時性的，重新部署或冷啟動後這張表會
    清空，既有 refresh token 因此驗不過。這是刻意接受的取捨——App 端收到
    refresh 失敗時會自動重新註冊，使用者無感。若日後改用 Firestore
    等持久化儲存，這個行為會自然消失。
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT revoked FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    return row is not None and not row["revoked"]


def revoke_device(device_id: str):
    """封鎖裝置：既有 refresh token 立即失效，access token 於過期後失效。"""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE devices SET revoked = 1 WHERE device_id = ?", (device_id,)
        )
        conn.commit()
