"""crawler/news_scraper.py — extract_region()。

這是全 repo 分支最密的純函式：地標別名、完整縣市名、縣市縮寫、行政區反推、
後綴補齊，五條路徑互相影響。它的輸出直接決定 `/api/news?region=` 的過濾結果
與 RAG 的事件脈絡，錯了會把外縣市的火災掛到使用者頭上。

測試分兩組：
  1. 目前正確的行為 → 一般測試，重構時必須維持
  2. 已知缺陷 → known_bug + xfail(strict=True)
"""

import pytest

from crawler.news_scraper import DISTRICTS, extract_region


# ── 地標別名（最高優先）─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text, expected",
    [
        ("竹科附近有濃煙", "新竹市"),
        ("中科園區異味擾民", "台中市"),
        ("南科廠區排放超標", "台南市"),
    ],
)
def test_landmark_aliases_take_priority(text, expected):
    assert extract_region(text) == expected


# ── 完整縣市名 + 行政區 ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text, expected",
    [
        ("台北市大安區發生火災", "台北市大安區"),
        ("高雄市林園區工廠排放", "高雄市林園區"),
        ("台中市西屯區空品不良", "台中市西屯區"),
        ("基隆市七堵區今晨濃煙", "基隆市七堵區"),
        ("雲林縣麥寮鄉工廠排放", "雲林縣麥寮鄉"),
        ("台東縣成功鎮起火", "台東縣成功鎮"),
    ],
)
def test_county_and_district_extraction(text, expected):
    assert extract_region(text) == expected


def test_traditional_tai_character_is_handled():
    """新聞來源常寫「臺北市」，DISTRICTS 用的是「台」，兩者必須都能解析。"""
    assert extract_region("臺北市大安區發生火災") == "台北市大安區"


# ── 縣市縮寫 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text, expected",
    [
        ("北市信義區火警", "台北市信義區"),
        ("竹市香山區異味", "新竹市香山區"),
        ("嘉縣朴子市火災", "嘉義縣朴子市"),
    ],
)
def test_county_abbreviations(text, expected):
    assert extract_region(text) == expected


def test_abbreviation_disambiguates_city_versus_county():
    """竹市／嘉市 與 竹縣／嘉縣 共用同一份行政區清單，靠強制後綴區分。"""
    assert extract_region("竹市香山區異味").startswith("新竹市")
    assert extract_region("嘉市東區異味").startswith("嘉義市")
    assert extract_region("嘉縣朴子市火災").startswith("嘉義縣")


def test_full_county_name_wins_over_district_reverse_lookup():
    """新竹縣竹北市：縣市名優先，且不得被誤判成新竹市。"""
    assert extract_region("新竹縣竹北市工廠起火") == "新竹縣竹北市"


# ── 找不到地名時的 fallback ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "全台空品拉警報",
        "空污紫爆",
        "工廠大火濃煙密布",
        "PM2.5超標",
    ],
)
def test_returns_placeholder_when_no_location_present(text):
    assert extract_region(text) == "台灣 (未指明特定縣市)"


# ── 不變條件 ─────────────────────────────────────────────────────────────────

def test_output_is_never_empty():
    for text in ("", "隨機文字", "12345"):
        assert extract_region(text)


def test_ambiguous_single_char_districts_do_not_trigger_reverse_lookup():
    """「中」「南」「北」等單字行政區不得單獨觸發地區判定。"""
    assert extract_region("中南部空品不良") == "台灣 (未指明特定縣市)"


# ── 已知缺陷 ─────────────────────────────────────────────────────────────────

_COMMON_WORDS_CONTAINING_DISTRICT_CHARS = [
    "最新空品警報",
    "創新科技園區排放",
    "新聞快訊：空氣品質不良",
]


@pytest.mark.known_bug
@pytest.mark.parametrize("text", _COMMON_WORDS_CONTAINING_DISTRICT_CHARS)
@pytest.mark.xfail(
    strict=True,
    reason="台南市「新市區」經 rstrip('區鄉鎮市') 後縮成單字「新」，卻不在 "
           "_AMBIGUOUS_DISTRICTS 白名單內。任何含「新」的標題（最新／創新／新聞）"
           "都會被反推成「台南市新區」，把全國新聞誤掛到台南。",
)
def test_common_words_are_not_mistaken_for_tainan_districts(text):
    assert extract_region(text) == "台灣 (未指明特定縣市)"


def test_rstrip_produces_single_character_districts():
    """記錄造成上述缺陷的根因：rstrip 會把「新市區」削成「新」。

    這個測試描述現況（非期望值），修正 rstrip 邏輯後應一併更新。
    """
    assert "新" in DISTRICTS["台南"]
    assert "左" in DISTRICTS["台南"]


@pytest.mark.known_bug
@pytest.mark.parametrize(
    "text, expected",
    [
        ("南投縣埔里鎮空品不良", "南投縣埔里鎮"),
        ("花蓮縣吉安鄉濃煙", "花蓮縣吉安鄉"),
        ("宜蘭縣羅東鎮異味", "宜蘭縣羅東鎮"),
        ("彰化縣鹿港鎮工廠大火", "彰化縣鹿港鎮"),
        ("屏東縣潮州鎮火警", "屏東縣潮州鎮"),
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="縣治與縣同名時（南投市／花蓮市／宜蘭市／彰化市／屏東市），"
           "_find_district 會先命中與縣同名的行政區並 break，"
           "把真正的鄉鎮蓋掉，輸出「南投縣南投」這種殘缺地名。",
)
def test_county_seat_does_not_shadow_the_real_district(text, expected):
    assert extract_region(text) == expected


@pytest.mark.known_bug
@pytest.mark.parametrize(
    "text, expected_prefix",
    [
        ("竹北市發生火警", "新竹縣"),
        ("竹北市工廠濃煙", "新竹縣"),
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="_COUNTY_ABBREVS 以子字串比對且「北市」排在最前面，"
           "「竹北市」會先命中「北市」而被判為台北市。"
           "縮寫比對必須加詞界檢查，或改以最長縮寫優先。",
)
def test_abbreviation_matching_does_not_match_across_word_boundaries(text, expected_prefix):
    assert extract_region(text).startswith(expected_prefix)


def test_district_matching_currently_depends_on_source_data_ordering():
    """記錄現況：同長度的行政區依 JSON 原始順序比對，先出現者勝。

    「苗栗縣頭份市」目前解析正確，純粹是因為「頭份」剛好排在「苗栗」前面；
    換一份 CityCountyData.json 就可能翻盤。改為最長匹配／位置優先後，
    這個測試應改寫為直接斷言解析結果。
    """
    districts = DISTRICTS["苗栗"]
    assert districts.index("頭份") < districts.index("苗栗")
    assert extract_region("苗栗縣頭份市空品不良") == "苗栗縣頭份市"
