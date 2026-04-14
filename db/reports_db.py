# db/reports_db.py
# 民眾回報 SQLite 資料庫模組

import sqlite3
import json
import os
from datetime import datetime, timedelta

# DB 放在 crawler/ 旁邊，跟 scraped 資料同目錄
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "crawler", "user_reports.db")


# ── 連線 ──────────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── 建表 / 初始化 ─────────────────────────────────────────────────────────────

def init_db():
    """建立資料表與索引（冪等，可重複呼叫）。"""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_reports (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                source               TEXT    DEFAULT '民眾回報',
                region               TEXT,
                category             TEXT,
                title                TEXT,
                summary              TEXT,
                url                  TEXT    DEFAULT '',
                latitude             REAL,
                longitude            REAL,
                event_type           TEXT,
                severity             TEXT,
                is_confirmed         INTEGER DEFAULT 0,
                published_at         TEXT,
                timestamp            TEXT,
                structured_event_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp    ON user_reports(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_region       ON user_reports(region)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_is_confirmed ON user_reports(is_confirmed)")
        conn.commit()
    print("✅ user_reports DB 初始化完成")


# ── 寫入 ──────────────────────────────────────────────────────────────────────

def insert_report(record: dict):
    """將一筆（已結構化的）回報存入 DB。"""
    se = record.get("structured_event") or {}
    se_json = json.dumps(se, ensure_ascii=False) if se else None

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_reports
                (source, region, category, title, summary, url,
                 latitude, longitude, event_type, severity, is_confirmed,
                 published_at, timestamp, structured_event_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("source", "民眾回報"),
                record.get("region", ""),
                record.get("category", ""),
                record.get("title", ""),
                record.get("summary", ""),
                record.get("url", ""),
                record.get("latitude"),
                record.get("longitude"),
                se.get("event_type") if se else None,
                se.get("severity") if se else None,
                1 if se.get("is_confirmed_pollution_event") else 0,
                record.get("published_at", datetime.now().isoformat()),
                record.get("timestamp", datetime.now().isoformat()),
                se_json,
            ),
        )
        conn.commit()


# ── 查詢 ──────────────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    se_json = d.pop("structured_event_json", None)
    d["structured_event"] = json.loads(se_json) if se_json else None
    d["is_confirmed"] = bool(d.get("is_confirmed", 0))
    return d


def get_recent_reports(hours: int = 24) -> list[dict]:
    """回傳最近 N 小時的全部回報（含未確認）。"""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_reports WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_reports() -> list[dict]:
    """回傳所有歷史回報。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_reports ORDER BY timestamp DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_confirmed_reports() -> list[dict]:
    """回傳所有已確認污染事件（供熱點分析用）。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_reports WHERE is_confirmed = 1 ORDER BY timestamp DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_recent_confirmed_by_county(county: str, hours: int = 24) -> list[dict]:
    """回傳近 N 小時、指定縣市的確認回報（供 RAG 事件描述用）。"""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM user_reports
            WHERE is_confirmed = 1
              AND timestamp >= ?
              AND region LIKE ?
            ORDER BY timestamp DESC
            """,
            (cutoff, f"%{county}%"),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── 遷移舊 JSON ───────────────────────────────────────────────────────────────

def migrate_from_json(json_path: str) -> int:
    """
    一次性：將舊 user_reports.json 遷移到 SQLite。
    回傳成功匯入的筆數。
    """
    if not os.path.exists(json_path):
        return 0

    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    count = 0
    for r in records:
        try:
            insert_report(r)
            count += 1
        except Exception as e:
            print(f"⚠️  遷移失敗（略過）：{e}")

    print(f"✅ 已從 JSON 遷移 {count} 筆回報到 SQLite")
    return count
