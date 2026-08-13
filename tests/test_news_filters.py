"""crawler/news_scraper.py — 規則層過濾（方法一）。

`is_realtime_event` 決定哪些新聞會進到 LLM 結構化階段，
因此它同時影響「使用者看到什麼」與「每小時要付多少 LLM 費用」。
"""

from datetime import datetime, timedelta, timezone

import pytest

from crawler.news_scraper import (
    NEWS_RETENTION_HOURS,
    NON_REALTIME_EXCLUSIONS,
    REALTIME_HINTS,
    is_realtime_event,
    is_within_retention,
)


# ── is_realtime_event ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "title",
    [
        "台中工廠今晨大火 濃煙竄天",
        "高雄昨日空品紫爆 民眾抱怨",
        "警消獲報趕往現場灌救",
        "台南某廠外洩異味 居民投訴",
        "西半部空品亮紅燈",
    ],
)
def test_realtime_events_are_kept(title):
    assert is_realtime_event(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "十年前大火回顧 當時災情慘重",
        "工廠空污案宣判 業者需賠償",
        "消防署舉行防災演習",
        "空污議題紀錄片上映",
        "環團呼籲政府加嚴空污標準",
    ],
)
def test_non_realtime_events_are_dropped(title):
    assert is_realtime_event(title) is False


def test_exclusion_beats_realtime_hint():
    """同時含排除詞與即時詞時，排除詞優先（避免把判決新聞當即時災情）。"""
    title = "今日法院判決 十年前工廠大火案"
    assert any(w in title for w in NON_REALTIME_EXCLUSIONS)
    assert any(w in title for w in REALTIME_HINTS)
    assert is_realtime_event(title) is False


def test_text_without_any_hint_is_dropped():
    assert is_realtime_event("空氣品質相關報導") is False


def test_summary_can_supply_the_realtime_hint():
    """公視／Yahoo 有 summary，標題沒有即時詞時應允許由摘要補足。"""
    assert is_realtime_event("某工廠狀況", "消防隊已於今晨到場灌救") is True


def test_empty_input_is_dropped():
    assert is_realtime_event("") is False
    assert is_realtime_event("", "") is False


# ── is_within_retention ──────────────────────────────────────────────────────

def test_rfc2822_timestamp_within_window_is_kept():
    """Google News / Yahoo RSS 用 RFC 2822 格式。"""
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    assert is_within_retention(recent.strftime("%a, %d %b %Y %H:%M:%S %z")) is True


def test_rfc2822_timestamp_outside_window_is_dropped():
    old = datetime.now(timezone.utc) - timedelta(hours=NEWS_RETENTION_HOURS + 5)
    assert is_within_retention(old.strftime("%a, %d %b %Y %H:%M:%S %z")) is False


def test_iso8601_timestamp_within_window_is_kept():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    assert is_within_retention(recent.isoformat().replace("+00:00", "Z")) is True


def test_iso8601_timestamp_outside_window_is_dropped():
    old = datetime.now(timezone.utc) - timedelta(hours=NEWS_RETENTION_HOURS + 5)
    assert is_within_retention(old.isoformat().replace("+00:00", "Z")) is False


@pytest.mark.parametrize("value", ["", None, "not a date", "2026-13-45"])
def test_unparseable_timestamps_are_dropped(value):
    """解析不了就丟掉，寧可漏也不要讓過期新聞混進來。"""
    assert is_within_retention(value) is False


def test_future_timestamp_is_kept():
    """時鐘微幅超前的來源不該被誤殺。"""
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert is_within_retention(future.strftime("%a, %d %b %Y %H:%M:%S %z")) is True
