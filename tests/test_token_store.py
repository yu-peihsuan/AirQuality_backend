"""fcm/token_store.py — 裝置 token 註冊與每日通知偏好。

這一層決定「推播要發給誰」。它同時也是目前架構風險最集中的地方：
單一 JSON 檔、整檔讀寫、無鎖、存在 Cloud Run 的暫時性磁碟上。

測試分成三組：
  1. 註冊與查詢的基本語意
  2. 每日通知偏好與 catch-up 語意
  3. 已知缺陷（known_bug）：縣市正規化、token 更新覆寫、失效 token 清理
"""

from datetime import datetime, timedelta, timezone

import pytest

TAIPEI = timezone(timedelta(hours=8))


def _today_tw() -> str:
    return datetime.now(TAIPEI).strftime("%Y-%m-%d")


def _seed_daily(store, token="tok-1", *, county="台南市", hour=8, minute=0,
                enabled=True, last_sent=""):
    """直接寫入一筆每日通知設定。

    刻意不走 set_daily_preference：那個函式會讀真實時鐘來決定要不要抑制當天推播，
    使得測試結果隨執行時間改變。要驗 get_due_daily_tokens 的比對邏輯時，
    狀態必須是確定的。
    """
    store._save([{
        "token": token,
        "county": county,
        "lat": None,
        "lng": None,
        "conditions": "",
        "daily_enabled": enabled,
        "daily_hour": hour,
        "daily_minute": minute,
        "daily_last_sent": last_sent,
    }])


# ── 註冊與查詢 ───────────────────────────────────────────────────────────────

def test_register_then_lookup_by_county(token_store):
    token_store.register_token("tok-1", county="台南市", lat=23.0, lng=120.2)
    assert token_store.get_tokens_by_county("台南市") == ["tok-1"]


def test_register_is_idempotent_for_the_same_token(token_store):
    token_store.register_token("tok-1", county="台南市")
    token_store.register_token("tok-1", county="台南市")
    assert token_store.get_all_tokens() == ["tok-1"]


def test_re_register_updates_county_in_place(token_store):
    token_store.register_token("tok-1", county="台南市")
    token_store.register_token("tok-1", county="台北市")
    assert token_store.get_tokens_by_county("台南市") == []
    assert token_store.get_tokens_by_county("台北市") == ["tok-1"]


def test_lookup_for_unknown_county_returns_empty(token_store):
    token_store.register_token("tok-1", county="台南市")
    assert token_store.get_tokens_by_county("花蓮縣") == []


def test_get_all_tokens_on_empty_store(token_store):
    assert token_store.get_all_tokens() == []


def test_get_token_record_returns_none_for_unknown_token(token_store):
    assert token_store.get_token_record("nope") is None


def test_get_token_record_exposes_county_and_coords(token_store):
    token_store.register_token("tok-1", county="台南市", lat=23.0, lng=120.2)
    rec = token_store.get_token_record("tok-1")
    assert rec["county"] == "台南市"
    assert rec["lat"] == pytest.approx(23.0)
    assert rec["lng"] == pytest.approx(120.2)


# ── 敏感族群篩選 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "conditions, is_sensitive",
    [
        ("氣喘", True),
        ("心血管疾病", True),
        ("氣喘,高血壓", True),
        ("65歲以上", True),
        ("18歲以下", True),
        ("懷孕中", True),
        ("", False),
        ("無", False),
        ("近視", False),
    ],
)
def test_sensitive_token_filter(token_store, conditions, is_sensitive):
    token_store.register_token("tok-1", county="台南市", conditions=conditions)
    result = token_store.get_sensitive_tokens_by_county("台南市")
    assert (result == ["tok-1"]) is is_sensitive


def test_sensitive_filter_respects_county_boundary(token_store):
    token_store.register_token("tok-1", county="台南市", conditions="氣喘")
    assert token_store.get_sensitive_tokens_by_county("台北市") == []


# ── 地理鄰近查詢 ─────────────────────────────────────────────────────────────

def test_get_tokens_near_includes_only_devices_within_radius(token_store):
    token_store.register_token("近", county="台南市", lat=23.000, lng=120.200)
    token_store.register_token("遠", county="台北市", lat=25.048, lng=121.532)
    assert token_store.get_tokens_near(23.0, 120.2, radius_km=5.0) == ["近"]


def test_get_tokens_near_skips_devices_without_coordinates(token_store):
    token_store.register_token("無座標", county="台南市")
    assert token_store.get_tokens_near(23.0, 120.2, radius_km=50.0) == []


def test_get_tokens_near_radius_is_inclusive_of_boundary(token_store):
    """半徑邊界上的裝置應被包含（<=），避免臨界值漏推。"""
    token_store.register_token("邊界", county="台南市", lat=23.0, lng=120.2)
    assert token_store.get_tokens_near(23.0, 120.2, radius_km=0.0) == ["邊界"]


# ── 每日通知偏好 ─────────────────────────────────────────────────────────────

def test_set_daily_preference_for_unregistered_token_creates_a_record(token_store):
    token_store.set_daily_preference("brand-new", enabled=True, hour=23, minute=59)
    rec = token_store.get_token_record("brand-new")
    assert rec["daily_enabled"] is True
    assert rec["daily_hour"] == 23


def test_disabling_daily_preference_removes_it_from_due_list(token_store):
    _seed_daily(token_store, hour=8, minute=0)
    token_store.set_daily_preference("tok-1", enabled=False)
    assert token_store.get_due_daily_tokens(23, 59, _today_tw()) == []


def test_due_list_excludes_tokens_without_a_county(token_store):
    """不知道裝置在哪個縣市就無從產生摘要內容，必須排除。"""
    _seed_daily(token_store, county="", hour=8, minute=0)
    assert token_store.get_due_daily_tokens(23, 59, _today_tw()) == []


def test_due_list_excludes_disabled_tokens(token_store):
    _seed_daily(token_store, hour=8, minute=0, enabled=False)
    assert token_store.get_due_daily_tokens(23, 59, _today_tw()) == []


def test_due_list_excludes_time_not_yet_reached(token_store):
    _seed_daily(token_store, hour=20, minute=0)
    assert token_store.get_due_daily_tokens(8, 0, _today_tw()) == []


def test_due_list_includes_token_at_the_exact_minute(token_store):
    _seed_daily(token_store, hour=8, minute=30)
    due = token_store.get_due_daily_tokens(8, 30, _today_tw())
    assert [t["token"] for t in due] == ["tok-1"]


def test_due_list_uses_catch_up_semantics(token_store):
    """排程被延遲時（例如 Cloud Run CPU 節流），下一輪仍要補發而非整天漏掉。"""
    _seed_daily(token_store, hour=8, minute=0)
    due = token_store.get_due_daily_tokens(8, 45, _today_tw())
    assert [t["token"] for t in due] == ["tok-1"]


def test_due_list_compares_hour_before_minute(token_store):
    """09:05 已過 08:30；不得只比較分鐘而漏判。"""
    _seed_daily(token_store, hour=8, minute=30)
    assert token_store.get_due_daily_tokens(9, 5, _today_tw())


def test_mark_daily_sent_prevents_a_second_push_the_same_day(token_store):
    _seed_daily(token_store, hour=8, minute=0)
    today = _today_tw()
    assert token_store.get_due_daily_tokens(8, 0, today)

    token_store.mark_daily_sent("tok-1", today)
    assert token_store.get_due_daily_tokens(8, 0, today) == []


def test_mark_daily_sent_does_not_block_the_next_day(token_store):
    _seed_daily(token_store, hour=8, minute=0, last_sent="2020-01-01")
    assert token_store.get_due_daily_tokens(8, 0, _today_tw())


def test_setting_a_time_already_passed_today_suppresses_todays_push(token_store):
    """一設定就馬上收到通知是不合理的體驗，應順延到明天。"""
    token_store.register_token("tok-1", county="台南市")
    token_store.set_daily_preference("tok-1", enabled=True, hour=0, minute=0)
    assert token_store.get_due_daily_tokens(23, 59, _today_tw()) == []


# ── 已知缺陷 ─────────────────────────────────────────────────────────────────

@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="App 端直接把 MOENV 的原始縣市字串（「臺南市」）當 key 上傳，"
           "而 gis.COUNTY_CENTROIDS 用的是「台南市」；register_token 未做正規化，"
           "導致民眾回報的擴散推播對臺北／臺中／臺南／臺東四縣市完全查無裝置。",
)
def test_county_lookup_is_normalized_between_tai_variants(token_store):
    """「臺」與「台」必須在寫入時就正規化，讓兩種寫法查得到同一批裝置。"""
    token_store.register_token("tok-1", county="臺南市")
    assert token_store.get_tokens_by_county("台南市") == ["tok-1"]


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="MyFirebaseMessagingService.onNewToken 以 county=\"\" 呼叫註冊，"
           "register_token 無條件覆寫欄位，把已知縣市清空，"
           "使裝置在下次 GPS 定位前收不到任何縣市推播。",
)
def test_registering_with_blank_county_preserves_the_known_county(token_store):
    token_store.register_token("tok-1", county="台南市", lat=23.0, lng=120.2)
    token_store.register_token("tok-1", county="")
    assert token_store.get_tokens_by_county("台南市") == ["tok-1"]


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="同上：conditions 也被空字串覆寫，敏感族群設定在 token 更新後遺失。",
)
def test_registering_with_blank_conditions_preserves_health_profile(token_store):
    token_store.register_token("tok-1", county="台南市", conditions="氣喘")
    token_store.register_token("tok-1", county="台南市", conditions="")
    assert token_store.get_sensitive_tokens_by_county("台南市") == ["tok-1"]


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="目前沒有任何移除 token 的介面。FCM 回報 UNREGISTERED 的失效 token "
           "會永久累積在 JSON 檔中，每次推播都算一次 failure。",
)
def test_store_exposes_a_way_to_remove_stale_tokens(token_store):
    token_store.register_token("tok-1", county="台南市")
    remove = getattr(token_store, "remove_token", None)
    assert remove is not None, "需要 remove_token() 以清理 FCM 回報失效的 token"
    remove("tok-1")
    assert token_store.get_all_tokens() == []


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="整檔 read-modify-write 且無鎖；FastAPI 的 sync endpoint 跑在 threadpool 上，"
           "併發註冊會造成 lost update。修正方向為改用 Firestore 或加檔案鎖。",
)
def test_concurrent_registration_does_not_lose_updates(token_store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda i: token_store.register_token(f"tok-{i}", county="台南市"),
            range(40),
        ))

    assert len(token_store.get_all_tokens()) == 40
