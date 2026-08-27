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


def is_revoked(device_id: str) -> bool:
    """裝置是否已被明確封鎖。

    查無紀錄一律回 False。理由同 is_active 的說明：容器重啟後這張表會清空，
    此時若把「查不到」視為封鎖，等於一次擋掉所有持有有效憑證的使用者。
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT revoked FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    return row is not None and bool(row["revoked"])


def revoke_device(device_id: str) -> bool:
    """封鎖裝置。回傳是否真的有這筆紀錄可封鎖。"""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE devices SET revoked = 1 WHERE device_id = ?", (device_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def restore_device(device_id: str) -> bool:
    """解除封鎖。回傳是否真的有這筆紀錄可解除。"""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE devices SET revoked = 0 WHERE device_id = ?", (device_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def list_devices() -> list[dict]:
    """列出所有已註冊裝置，最近使用的排前面。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT device_id, created, last_seen, revoked FROM devices "
            "ORDER BY last_seen DESC"
        ).fetchall()
    return [
        {
            "device_id": r["device_id"],
            "created": r["created"],
            "last_seen": r["last_seen"],
            "revoked": bool(r["revoked"]),
        }
        for r in rows
    ]
