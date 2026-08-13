"""db/reports_db.py — 民眾回報儲存層。

這一層有兩組不同性質的測試：

A. 查詢語意測試：直接餵入明確的時間字串，驗證時間窗、過濾、序列化行為。
   這些測試與行程時區無關，任何情境下都必須通過。

B. 時區契約測試（見 test_timezone_contract.py）：驗證「寫入端產生的時間戳」
   與「查詢端計算的 cutoff」在同一個時間基準上。這組目前是已知缺陷。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

TAIPEI = timezone(timedelta(hours=8))


def _ts(dt: datetime) -> str:
    """把 aware datetime 轉成 DB 內實際使用的字串格式。"""
    return dt.replace(tzinfo=None).isoformat()


def _report(summary="濃煙異味", *, region="台南市中西區", category="fire",
            device_id=None, confirmed=True, timestamp=None, lat=23.0, lng=120.2):
    ts = timestamp or _ts(datetime.now())
    return {
        "source": "民眾回報",
        "region": region,
        "category": category,
        "title": f"[{category}] {region}",
        "summary": summary,
        "url": "",
        "latitude": lat,
        "longitude": lng,
        "published_at": ts,
        "timestamp": ts,
        "device_id": device_id,
        "verify_sources": [],
        "structured_event": {
            "is_confirmed_pollution_event": confirmed,
            "event_type": "fire",
            "severity": "high",
        },
    }


# ── 建表與冪等性 ─────────────────────────────────────────────────────────────

def test_init_db_is_idempotent(reports_db):
    reports_db.init_db()
    reports_db.init_db()
    assert reports_db.get_all_reports() == []


def test_init_db_preserves_existing_rows(reports_db):
    reports_db.insert_report(_report("第一筆"))
    reports_db.init_db()
    assert len(reports_db.get_all_reports()) == 1


# ── 寫入與序列化 ─────────────────────────────────────────────────────────────

def test_insert_and_read_roundtrip(reports_db):
    reports_db.insert_report(_report("工廠冒黑煙"))
    rows = reports_db.get_all_reports()
    assert len(rows) == 1
    assert rows[0]["summary"] == "工廠冒黑煙"
    assert rows[0]["region"] == "台南市中西區"
    assert rows[0]["latitude"] == pytest.approx(23.0)


def test_structured_event_roundtrips_as_nested_dict(reports_db):
    reports_db.insert_report(_report())
    row = reports_db.get_all_reports()[0]
    assert row["structured_event"]["event_type"] == "fire"
    assert row["structured_event"]["severity"] == "high"


def test_is_confirmed_is_exposed_as_bool_not_int(reports_db):
    """App 端依這個欄位顯示【已證實】/【未證實】，型別必須是 bool。"""
    reports_db.insert_report(_report(confirmed=True))
    reports_db.insert_report(_report("模糊描述", confirmed=False))
    values = [r["is_confirmed"] for r in reports_db.get_all_reports()]
    assert all(isinstance(v, bool) for v in values)
    assert sorted(values) == [False, True]


def test_device_id_is_never_exposed_to_clients(reports_db):
    """裝置識別碼是個資，查詢結果不得外流。"""
    reports_db.insert_report(_report(device_id="device-abc-123"))
    for getter in (reports_db.get_all_reports, reports_db.get_recent_reports):
        rows = getter()
        assert rows
        assert "device_id" not in rows[0]
        assert "device-abc-123" not in json.dumps(rows, ensure_ascii=False)


def test_verify_sources_roundtrips_as_list(reports_db):
    rec = _report()
    rec["verify_sources"] = ["官方火災警示", "測站數據異常"]
    reports_db.insert_report(rec)
    assert reports_db.get_all_reports()[0]["verify_sources"] == ["官方火災警示", "測站數據異常"]


def test_verify_sources_defaults_to_empty_list(reports_db):
    rec = _report()
    rec.pop("verify_sources")
    reports_db.insert_report(rec)
    assert reports_db.get_all_reports()[0]["verify_sources"] == []


def test_insert_tolerates_missing_structured_event(reports_db):
    """LLM 分析失敗時 structured_event 是 None，仍必須能存進去。"""
    rec = _report()
    rec["structured_event"] = None
    reports_db.insert_report(rec)
    row = reports_db.get_all_reports()[0]
    assert row["structured_event"] is None
    assert row["is_confirmed"] is False


# ── 時間窗查詢（明確時間戳，與行程時區無關）─────────────────────────────────

def test_get_recent_reports_excludes_rows_outside_the_window(reports_db):
    now = datetime.now()
    reports_db.insert_report(_report("剛剛", timestamp=_ts(now - timedelta(hours=1))))
    reports_db.insert_report(_report("前天", timestamp=_ts(now - timedelta(hours=48))))
    recent = reports_db.get_recent_reports(hours=24)
    assert [r["summary"] for r in recent] == ["剛剛"]


def test_get_recent_reports_ordered_newest_first(reports_db):
    now = datetime.now()
    for label, delta in (("舊", 5), ("新", 1), ("中", 3)):
        reports_db.insert_report(_report(label, timestamp=_ts(now - timedelta(hours=delta))))
    assert [r["summary"] for r in reports_db.get_recent_reports(hours=24)] == ["新", "中", "舊"]


def test_get_recent_reports_by_region_matches_substring(reports_db):
    now = datetime.now()
    ts = _ts(now - timedelta(minutes=5))
    reports_db.insert_report(_report("台南事件", region="台南市中西區", timestamp=ts))
    reports_db.insert_report(_report("台北事件", region="台北市大安區", timestamp=ts))
    rows = reports_db.get_recent_reports_by_region("台南市", hours=24)
    assert [r["summary"] for r in rows] == ["台南事件"]


def test_get_confirmed_reports_filters_unconfirmed(reports_db):
    ts = _ts(datetime.now() - timedelta(minutes=5))
    reports_db.insert_report(_report("真事件", confirmed=True, timestamp=ts))
    reports_db.insert_report(_report("假事件", confirmed=False, timestamp=ts))
    rows = reports_db.get_confirmed_reports(hours=24)
    assert [r["summary"] for r in rows] == ["真事件"]


def test_get_recent_confirmed_by_county_filters_both_county_and_confirmation(reports_db):
    ts = _ts(datetime.now() - timedelta(minutes=5))
    reports_db.insert_report(_report("台南真", region="台南市安南區", confirmed=True, timestamp=ts))
    reports_db.insert_report(_report("台南假", region="台南市安南區", confirmed=False, timestamp=ts))
    reports_db.insert_report(_report("台北真", region="台北市大安區", confirmed=True, timestamp=ts))
    rows = reports_db.get_recent_confirmed_by_county("台南市", hours=24)
    assert [r["summary"] for r in rows] == ["台南真"]


# ── 防灌水：頻率限制與去重 ───────────────────────────────────────────────────

def test_count_recent_by_device_returns_zero_for_unknown_device(reports_db):
    assert reports_db.count_recent_by_device("no-such-device", minutes=3) == 0


def test_count_recent_by_device_returns_zero_for_empty_device_id(reports_db):
    """device_id 為空字串或 None 時不得誤判成「同一台裝置」而集體限流。"""
    assert reports_db.count_recent_by_device("", minutes=3) == 0
    assert reports_db.count_recent_by_device(None, minutes=3) == 0


def test_count_recent_by_device_counts_only_that_device(reports_db):
    ts = _ts(datetime.now() - timedelta(seconds=30))
    reports_db.insert_report(_report("A的回報", device_id="dev-a", timestamp=ts))
    reports_db.insert_report(_report("B的回報", device_id="dev-b", timestamp=ts))
    assert reports_db.count_recent_by_device("dev-a", minutes=3) == 1


def test_count_recent_by_device_ignores_rows_outside_the_window(reports_db):
    old = _ts(datetime.now() - timedelta(minutes=10))
    reports_db.insert_report(_report("十分鐘前", device_id="dev-a", timestamp=old))
    assert reports_db.count_recent_by_device("dev-a", minutes=3) == 0


def test_exists_similar_recent_detects_exact_duplicate(reports_db):
    ts = _ts(datetime.now() - timedelta(minutes=1))
    reports_db.insert_report(_report("附近有濃煙", region="台南市中西區",
                                     category="fire", timestamp=ts))
    assert reports_db.exists_similar_recent("台南市中西區", "fire", "附近有濃煙", hours=6) is True


def test_exists_similar_recent_is_false_when_any_field_differs(reports_db):
    ts = _ts(datetime.now() - timedelta(minutes=1))
    reports_db.insert_report(_report("附近有濃煙", region="台南市中西區",
                                     category="fire", timestamp=ts))
    assert reports_db.exists_similar_recent("台北市大安區", "fire", "附近有濃煙", hours=6) is False
    assert reports_db.exists_similar_recent("台南市中西區", "odor", "附近有濃煙", hours=6) is False
    assert reports_db.exists_similar_recent("台南市中西區", "fire", "別的描述", hours=6) is False


def test_exists_similar_recent_ignores_rows_outside_the_window(reports_db):
    old = _ts(datetime.now() - timedelta(hours=10))
    reports_db.insert_report(_report("附近有濃煙", region="台南市中西區",
                                     category="fire", timestamp=old))
    assert reports_db.exists_similar_recent("台南市中西區", "fire", "附近有濃煙", hours=6) is False


# ── schema 遷移 ──────────────────────────────────────────────────────────────

def test_init_db_adds_missing_columns_to_legacy_table(tmp_path, monkeypatch):
    """舊版 DB 沒有 device_id / verify_sources 欄位，init_db 必須補上而不炸掉。"""
    import sqlite3

    import db.reports_db as mod

    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(mod, "DB_PATH", str(db_path))

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE user_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT, region TEXT, category TEXT, title TEXT,
                summary TEXT, url TEXT, latitude REAL, longitude REAL,
                event_type TEXT, severity TEXT, is_confirmed INTEGER,
                published_at TEXT, timestamp TEXT, structured_event_json TEXT
            )
        """)
        conn.commit()

    mod.init_db()
    mod.insert_report(_report(device_id="dev-x"))
    assert len(mod.get_all_reports()) == 1
