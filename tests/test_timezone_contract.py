"""時區契約：寫入端與查詢端必須用同一個時間基準。

背景
────
`main.submit_report` 以「台灣牆上時間、去掉 tzinfo」寫入 timestamp：

    datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat()

而 `db/reports_db.py` 的所有 cutoff 都用 `datetime.now()`（行程本地時間）。

開發機的 TZ 是 Asia/Taipei，兩者剛好一致，所以本機測起來正常；
Cloud Run 容器的 TZ 是 UTC，於是資料庫裡的時間戳永遠比 cutoff 基準「早 8 小時」，
所有時間窗都被放大 8 小時：

    裝置頻率限制  3 分鐘 → 8 小時 3 分
    相同內容去重  6 小時 → 14 小時
    近期回報查詢 24 小時 → 32 小時

這組測試把「不論行程時區為何，時間窗都必須正確」這件事釘住。
標記 known_bug 的案例在修好之前是 xfail(strict=True)；
一旦修好就會變成 XPASS 而讓測試套件失敗，強迫回來移除標記。
"""

from datetime import datetime, timedelta, timezone

import pytest

TAIPEI = timezone(timedelta(hours=8))


def _stored_timestamp(moment_utc: datetime) -> str:
    """重現 main.submit_report 目前寫入 DB 的時間戳格式。

    參數是該筆回報實際發生的 UTC 時刻。
    """
    return moment_utc.astimezone(TAIPEI).replace(tzinfo=None).isoformat()


def _report(summary, *, device_id=None, region="台南市中西區",
            category="fire", timestamp=None):
    return {
        "source": "民眾回報",
        "region": region,
        "category": category,
        "title": f"[{category}] {region}",
        "summary": summary,
        "url": "",
        "latitude": 23.0,
        "longitude": 120.2,
        "published_at": timestamp,
        "timestamp": timestamp,
        "device_id": device_id,
        "verify_sources": [],
        "structured_event": {
            "is_confirmed_pollution_event": True,
            "event_type": "fire",
            "severity": "high",
        },
    }


# ── 開發機情境（Asia/Taipei）：目前是通過的 ─────────────────────────────────

def test_rate_limit_window_is_correct_under_taipei_timezone(reports_db, tz):
    tz("Asia/Taipei")
    ten_min_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    reports_db.insert_report(
        _report("十分鐘前", device_id="dev-a", timestamp=_stored_timestamp(ten_min_ago))
    )
    assert reports_db.count_recent_by_device("dev-a", minutes=3) == 0


# ── 正式環境情境（UTC）：目前是壞的 ─────────────────────────────────────────

@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="submit_report 寫入 UTC+8 牆上時間，count_recent_by_device 用 datetime.now()（UTC）"
           "計算 cutoff，導致 3 分鐘的頻率限制在 Cloud Run 上變成 8 小時 3 分。",
)
def test_rate_limit_window_is_correct_under_utc_timezone(reports_db, tz):
    """在 UTC 伺服器上，10 分鐘前的回報不得再落在 3 分鐘的限流窗內。"""
    tz("UTC")
    ten_min_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    reports_db.insert_report(
        _report("十分鐘前", device_id="dev-a", timestamp=_stored_timestamp(ten_min_ago))
    )
    assert reports_db.count_recent_by_device("dev-a", minutes=3) == 0


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="同上：去重窗在 UTC 伺服器上由 6 小時膨脹為 14 小時，"
           "使用者隔天回報同一地點會被誤判為重複而靜默丟棄。",
)
def test_dedup_window_is_correct_under_utc_timezone(reports_db, tz):
    """8 小時前的相同回報已超出 6 小時去重窗，應允許再次回報。"""
    tz("UTC")
    eight_hours_ago = datetime.now(timezone.utc) - timedelta(hours=8)
    reports_db.insert_report(
        _report("附近有濃煙", timestamp=_stored_timestamp(eight_hours_ago))
    )
    assert reports_db.exists_similar_recent(
        "台南市中西區", "fire", "附近有濃煙", hours=6
    ) is False


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="同上：24 小時查詢在 UTC 伺服器上實際回傳 32 小時內的資料，"
           "熱點分析與 RAG 事件脈絡都會吃到過期事件。",
)
def test_recent_reports_window_is_correct_under_utc_timezone(reports_db, tz):
    """30 小時前的回報不該出現在 24 小時查詢結果中。"""
    tz("UTC")
    thirty_hours_ago = datetime.now(timezone.utc) - timedelta(hours=30)
    reports_db.insert_report(
        _report("三十小時前", timestamp=_stored_timestamp(thirty_hours_ago))
    )
    assert reports_db.get_recent_reports(hours=24) == []


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="同上：熱點分析的時間窗同樣被放大，已結束的事件仍會被算進熱點。",
)
def test_confirmed_reports_window_is_correct_under_utc_timezone(reports_db, tz):
    tz("UTC")
    thirty_hours_ago = datetime.now(timezone.utc) - timedelta(hours=30)
    reports_db.insert_report(
        _report("三十小時前", timestamp=_stored_timestamp(thirty_hours_ago))
    )
    assert reports_db.get_confirmed_reports(hours=24) == []


# ── 時區無關性：修好之後兩種時區的行為必須一致 ─────────────────────────────

@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="時間窗行為目前依賴行程時區；修正後兩種 TZ 下的結果必須完全一致。",
)
def test_query_results_are_identical_across_server_timezones(reports_db, tz):
    now_utc = datetime.now(timezone.utc)
    for hours, label in ((1, "一小時前"), (12, "十二小時前"), (30, "三十小時前")):
        reports_db.insert_report(
            _report(label, timestamp=_stored_timestamp(now_utc - timedelta(hours=hours)))
        )

    tz("Asia/Taipei")
    taipei = {r["summary"] for r in reports_db.get_recent_reports(hours=24)}

    tz("UTC")
    utc = {r["summary"] for r in reports_db.get_recent_reports(hours=24)}

    assert taipei == utc == {"一小時前", "十二小時前"}


# ── 每日通知偏好設定的時間基準 ───────────────────────────────────────────────

def test_daily_preference_uses_taiwan_time_regardless_of_server_timezone(token_store, tz):
    """set_daily_preference 依「台灣時間是否已過設定時刻」決定要不要抑制今天的推播。

    這個判斷必須以 UTC+8 進行，不能跟著行程時區跑；否則同一個設定在
    Cloud Run（UTC）與開發機（Asia/Taipei）會得到相反的結果。
    """
    today_tw = datetime.now(TAIPEI).strftime("%Y-%m-%d")

    tz("UTC")
    token_store.register_token("tok-utc", county="台南市")
    token_store.set_daily_preference("tok-utc", enabled=True, hour=0, minute=0)

    tz("Asia/Taipei")
    token_store.register_token("tok-tpe", county="台南市")
    token_store.set_daily_preference("tok-tpe", enabled=True, hour=0, minute=0)

    records = {t["token"]: t for t in token_store._load()}
    # 00:00 在台灣時間必定已過 → 兩者都應被抑制到明天，且結果一致
    assert records["tok-utc"]["daily_last_sent"] == today_tw
    assert records["tok-utc"]["daily_last_sent"] == records["tok-tpe"]["daily_last_sent"]
