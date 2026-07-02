# db/firestore_reports.py
# 民眾回報 Firestore 資料庫模組（取代 reports_db.py）

import os
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta

_KEY_PATH = os.path.join(
    os.path.dirname(__file__), "..",
    "serviceAccountKey.json"
)


def _get_db():
    """取得 Firestore client，若 Firebase 尚未初始化則自動初始化。"""
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(_KEY_PATH)
        firebase_admin.initialize_app(cred)
    return firestore.client()


def init_db():
    """Firestore 不需建表，呼叫此函式確保 Firebase 已初始化。"""
    _get_db()
    print("✅ Firestore 連線初始化完成")


def insert_report(record: dict):
    """將一筆（已結構化的）回報存入 Firestore。"""
    db = _get_db()
    se = record.get("structured_event") or {}

    doc = {
        "source":       record.get("source", "民眾回報"),
        "region":       record.get("region", ""),
        "category":     record.get("category", ""),
        "title":        record.get("title", ""),
        "summary":      record.get("summary", ""),
        "url":          record.get("url", ""),
        "latitude":     record.get("latitude"),
        "longitude":    record.get("longitude"),
        "event_type":   se.get("event_type"),
        "severity":     se.get("severity"),
        "is_confirmed": bool(se.get("is_confirmed_pollution_event")),
        "published_at": record.get("published_at", datetime.now().isoformat()),
        "timestamp":    record.get("timestamp", datetime.now().isoformat()),
        "structured_event": se,
    }
    db.collection("user_reports").add(doc)


def _doc_to_dict(doc) -> dict:
    """Firestore document → Python dict（附加文件 ID）。"""
    d = doc.to_dict()
    d["id"] = doc.id
    return d


def get_recent_reports(hours: int = 24) -> list[dict]:
    """回傳最近 N 小時的全部回報（含未確認）。"""
    db = _get_db()
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    docs = (
        db.collection("user_reports")
        .where("timestamp", ">=", cutoff)
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .stream()
    )
    return [_doc_to_dict(d) for d in docs]


def get_all_reports() -> list[dict]:
    """回傳所有歷史回報。"""
    db = _get_db()
    docs = (
        db.collection("user_reports")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .stream()
    )
    return [_doc_to_dict(d) for d in docs]


def get_confirmed_reports() -> list[dict]:
    """回傳所有已確認污染事件（供熱點分析用）。"""
    db = _get_db()
    docs = (
        db.collection("user_reports")
        .where("is_confirmed", "==", True)
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .stream()
    )
    return [_doc_to_dict(d) for d in docs]


def get_recent_confirmed_by_county(county: str, hours: int = 24) -> list[dict]:
    """回傳近 N 小時、指定縣市的確認回報（供 RAG 事件描述用）。"""
    db = _get_db()
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    docs = (
        db.collection("user_reports")
        .where("is_confirmed", "==", True)
        .where("timestamp", ">=", cutoff)
        .stream()
    )
    # Firestore 不支援 LIKE，在 Python 端過濾縣市
    results = []
    for d in docs:
        data = d.to_dict()
        if county in data.get("region", ""):
            data["id"] = d.id
            results.append(data)
    return results


def migrate_from_json(json_path: str) -> int:
    """相容舊介面，Firestore 版本不需遷移。"""
    return 0
